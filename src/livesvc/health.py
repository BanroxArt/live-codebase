"""Liveness and readiness.

``/healthz`` answers "is the process alive". It must not touch dependencies;
if it did, a database blip would make the orchestrator restart every pod at
once.

``/readyz`` answers "should traffic come here". It runs registered checks
with a time budget and fails as soon as the process starts draining, so the
load balancer stops sending new requests before shutdown.

A check that times out keeps running; Python cannot cancel a thread. If the
next probe started another copy, a hung dependency would fill the pool with
stuck checks and every later probe would time out waiting for a worker. So
each check runs at most once at a time. A probe that finds a run already in
flight waits for it, up to that run's own deadline, instead of starting a
second copy; a run past its deadline is reported as "still running".
Checks run in parallel, so a probe takes about one timeout, not one per check.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass

Check = Callable[[], None]  # raise to report failure


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


class Health:
    def __init__(self, timeout_s: float = 1.0, max_workers: int = 8) -> None:
        self.timeout_s = timeout_s
        self._checks: dict[str, Check] = {}
        self._inflight: dict[str, tuple[Future[None], float]] = {}
        self._lock = threading.Lock()
        self._draining = threading.Event()
        self._started = time.monotonic()
        self._max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="readyz")

    def register(self, name: str, check: Check) -> None:
        if name not in self._checks and len(self._checks) >= self._max_workers:
            # One worker per check is what keeps a hung check from starving the rest.
            raise ValueError(f"more than {self._max_workers} readiness checks")
        self._checks[name] = check

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def drain(self) -> None:
        self._draining.set()

    @property
    def draining(self) -> bool:
        return self._draining.is_set()

    def uptime(self) -> float:
        return time.monotonic() - self._started

    def _start(self, name: str, check: Check) -> tuple[Future[None], float, bool]:
        """Submit ``check`` unless a previous run is still going. Returns (future, t0, fresh)."""
        with self._lock:
            prev = self._inflight.get(name)
            if prev is not None and not prev[0].done():
                return prev[0], prev[1], False
            fut = self._pool.submit(check)
            t0 = time.perf_counter()
            self._inflight[name] = (fut, t0)
            return fut, t0, True

    @staticmethod
    def _result(name: str, fut: Future[None], t0: float) -> CheckResult:
        ms = (time.perf_counter() - t0) * 1000
        if not fut.done():
            return CheckResult(name, False, "timeout", ms)
        exc = fut.exception()
        if exc is not None:
            # Class name only: exception text can carry hostnames or DSNs.
            return CheckResult(name, False, type(exc).__name__, ms)
        return CheckResult(name, True, "", ms)

    def _wait(self, started: dict[str, tuple[Future[None], float, bool]]) -> None:
        """Wait for each run until its own deadline (its start plus the timeout).

        A run another probe started a moment ago gets the rest of its budget,
        so concurrent probes share one run instead of failing each other. A
        run that is already past its deadline is not waited on at all.
        """
        pending = {fut: t0 + self.timeout_s for fut, t0, _ in started.values()}
        while pending:
            now = time.perf_counter()
            pending = {f: d for f, d in pending.items() if not f.done() and d > now}
            if not pending:
                return
            wait_futures(
                list(pending), timeout=min(pending.values()) - now, return_when=FIRST_COMPLETED
            )

    def ready(self) -> tuple[bool, list[CheckResult]]:
        if self.draining:
            return False, [CheckResult("draining", False, "shutting down")]
        started = {n: self._start(n, c) for n, c in self._checks.items()}
        self._wait(started)
        results = []
        for name, (fut, t0, is_new) in started.items():
            if fut.done() or is_new:
                results.append(self._result(name, fut, t0))
            else:
                ms = (time.perf_counter() - t0) * 1000
                results.append(CheckResult(name, False, "still running", ms))
        return all(r.ok for r in results), results
