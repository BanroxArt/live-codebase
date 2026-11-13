"""Canary analysis: compare a canary's error rate and latency to the baseline.

Counters are cumulative, so compare like with like: deploy a fresh baseline
pod from the current release at the same time as the canary and scrape both
after the same soak period. Comparing a new canary to a pod that has been up
for a week mostly measures the week.

Feed it the two Prometheus text scrapes. ``decide`` returns promote, hold or
rollback with the reason, so a deploy script can act on it and a person can
read why.

Too little traffic is "hold", never "promote". A canary that served 12
requests has not shown anything.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass

LINE = re.compile(r"^(?P<name>[a-zA-Z_:][\w:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)$")
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True, slots=True)
class Stats:
    requests: float
    errors: float
    p95_s: float | None

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


@dataclass(frozen=True, slots=True)
class Thresholds:
    min_requests: int = 200
    max_error_rate_delta: float = 0.01  # absolute, e.g. 1 percentage point
    max_error_rate_ratio: float = 2.0
    max_p95_ratio: float = 1.5


@dataclass(frozen=True, slots=True)
class Verdict:
    action: str
    reasons: tuple[str, ...]


def parse_prometheus(text: str) -> list[tuple[str, dict[str, str], float]]:
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line.strip())
        if not m:
            continue
        labels = dict(LABEL.findall(m.group("labels") or ""))
        try:
            out.append((m.group("name"), labels, float(m.group("value"))))
        except ValueError:
            continue
    return out


def stats_from_metrics(
    text: str, skip_routes: tuple[str, ...] = ("/healthz", "/readyz", "/metrics")
) -> Stats:
    samples = parse_prometheus(text)
    req = err = 0.0
    buckets: dict[float, float] = {}
    for name, labels, value in samples:
        if labels.get("route") in skip_routes:
            continue
        if name == "http_requests_total":
            req += value
            if labels.get("status", "").startswith("5"):
                err += value
        elif name == "http_request_duration_seconds_bucket":
            le = labels.get("le", "")
            bound = float("inf") if le == "+Inf" else float(le)
            buckets[bound] = buckets.get(bound, 0.0) + value
    return Stats(req, err, _quantile(buckets, 0.95))


def _quantile(buckets: dict[float, float], q: float) -> float | None:
    if not buckets:
        return None
    ordered = sorted(buckets.items())
    total = ordered[-1][1]
    if not total:
        return None
    target = q * total
    prev_bound, prev_count = 0.0, 0.0
    for bound, count in ordered:
        if count >= target:
            if bound == float("inf"):
                return prev_bound
            # Linear interpolation inside the bucket, same as histogram_quantile.
            span = count - prev_count
            frac = (target - prev_count) / span if span else 1.0
            return prev_bound + (bound - prev_bound) * frac
        prev_bound, prev_count = bound, count
    return prev_bound


def decide(baseline: Stats, canary: Stats, t: Thresholds | None = None) -> Verdict:
    t = t or Thresholds()
    if canary.requests < t.min_requests:
        return Verdict(
            "hold", (f"canary served {canary.requests:g} requests, need {t.min_requests}",)
        )
    reasons = []
    delta = canary.error_rate - baseline.error_rate
    if delta > t.max_error_rate_delta and (
        baseline.error_rate == 0 or canary.error_rate / baseline.error_rate > t.max_error_rate_ratio
    ):
        reasons.append(f"error rate {canary.error_rate:.2%} vs baseline {baseline.error_rate:.2%}")
    if canary.p95_s is not None and baseline.p95_s:
        ratio = canary.p95_s / baseline.p95_s
        if ratio > t.max_p95_ratio:
            reasons.append(f"p95 {canary.p95_s * 1000:.0f}ms is {ratio:.1f}x baseline")
    if reasons:
        return Verdict("rollback", tuple(reasons))
    return Verdict("promote", ("within thresholds",))


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="livesvc-canary", description=__doc__)
    p.add_argument("baseline", help="metrics URL or file for the baseline")
    p.add_argument("canary", help="metrics URL or file for the canary")
    p.add_argument("--min-requests", type=int, default=200)
    args = p.parse_args(argv)

    def load(src: str) -> str:
        if src.startswith(("http://", "https://")):
            return _fetch(src)
        with open(src, encoding="utf-8") as fh:
            return fh.read()

    verdict = decide(
        stats_from_metrics(load(args.baseline)),
        stats_from_metrics(load(args.canary)),
        Thresholds(min_requests=args.min_requests),
    )
    print(json.dumps({"action": verdict.action, "reasons": list(verdict.reasons)}))
    return {"promote": 0, "hold": 3, "rollback": 1}[verdict.action]


if __name__ == "__main__":
    sys.exit(main())
