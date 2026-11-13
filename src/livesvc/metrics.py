"""Counters and histograms with Prometheus text output.

Enough for the golden signals (traffic, errors, latency) without a client
library. Label sets are bounded by the caller: use the route template
(``/api/users/{id}``), never the raw path, or cardinality explodes.
"""

from __future__ import annotations

import bisect
import threading
from collections import defaultdict

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    body = ",".join(
        f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for k, v in sorted(labels.items())
    )
    return "{" + body + "}"


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        self._hists: dict[str, dict[tuple, list]] = defaultdict(dict)
        self._help: dict[str, tuple[str, str]] = {}
        self._buckets: dict[str, tuple[float, ...]] = {}

    def counter(self, name: str, help_: str) -> None:
        self._help[name] = ("counter", help_)

    def histogram(
        self, name: str, help_: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        self._help[name] = ("histogram", help_)
        self._buckets[name] = tuple(sorted(buckets))

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._counters[name][key] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        bounds = self._buckets[name]
        with self._lock:
            h = self._hists[name].setdefault(key, [[0] * len(bounds), 0.0, 0])
            idx = bisect.bisect_left(bounds, value)
            if idx < len(bounds):
                h[0][idx] += 1
            h[1] += value
            h[2] += 1

    def value(self, name: str, **labels: str) -> float:
        return self._counters[name].get(tuple(sorted(labels.items())), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, (kind, help_) in sorted(self._help.items()):
                lines.append(f"# HELP {name} {help_}")
                lines.append(f"# TYPE {name} {kind}")
                if kind == "counter":
                    for key, v in sorted(self._counters[name].items()):
                        lines.append(f"{name}{_labels(dict(key))} {v:g}")
                else:
                    bounds = self._buckets[name]
                    for key, (counts, total, n) in sorted(self._hists[name].items()):
                        base = dict(key)
                        running = 0
                        for b, c in zip(bounds, counts, strict=True):
                            running += c
                            lines.append(
                                f"{name}_bucket{_labels({**base, 'le': f'{b:g}'})} {running}"
                            )
                        lines.append(f"{name}_bucket{_labels({**base, 'le': '+Inf'})} {n}")
                        lines.append(f"{name}_sum{_labels(base)} {total:g}")
                        lines.append(f"{name}_count{_labels(base)} {n}")
        return "\n".join(lines) + "\n"
