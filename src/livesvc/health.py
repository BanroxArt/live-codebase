"""Liveness and readiness.

``/healthz`` answers "is the process alive". It must not touch dependencies;
if it did, a database blip would make the orchestrator restart every pod at
once.

``/readyz`` answers "should traffic come here". It runs registered checks
with a time budget and fails as soon as the process starts draining, so the
load balancer stops sending new requests before shutdown.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

Check = Callable[[], None]  # raise to report failure


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


class Health:
    def __init__(self, timeout_s: float = 1.0) -> None:
        self.timeout_s = timeout_s
        self._checks: dict[str, Check] = {}
        self._draining = threading.Event()
        self._started = time.monotonic()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="readyz")

    def register(self, name: str, check: Check) -> None:
        self._checks[name] = check

    def drain(self) -> None:
        self._draining.set()

    @property
    def draining(self) -> bool:
        return self._draining.is_set()

    def uptime(self) -> float:
        return time.monotonic() - self._started

    def _run(self, name: str, check: Check) -> CheckResult:
        t0 = time.perf_counter()
        fut = self._pool.submit(check)
        try:
            fut.result(timeout=self.timeout_s)
        except FutureTimeout:
            return CheckResult(name, False, "timeout", (time.perf_counter() - t0) * 1000)
        except Exception as exc:
            # Class name only: exception text can carry hostnames or DSNs.
            return CheckResult(name, False, type(exc).__name__, (time.perf_counter() - t0) * 1000)
        return CheckResult(name, True, "", (time.perf_counter() - t0) * 1000)

    def ready(self) -> tuple[bool, list[CheckResult]]:
        if self.draining:
            return False, [CheckResult("draining", False, "shutting down")]
        results = [self._run(n, c) for n, c in self._checks.items()]
        return all(r.ok for r in results), results
