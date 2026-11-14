"""Micro-benchmarks. Run: PYTHONPATH=src python bench/run.py

Standard library timeit, best of five repeats. Numbers are per operation on
one CPU core; compare runs on the same machine, not across machines.
"""

from __future__ import annotations

import platform
import sqlite3
import sys
import timeit
from collections.abc import Callable

from livesvc.audit import AuditChain
from livesvc.auth import authenticate, mint
from livesvc.flags import Flags, bucket, parse_flags
from livesvc.metrics import Registry


def bench(name: str, fn: Callable[[], object], number: int, repeat: int = 5) -> None:
    best = min(timeit.repeat(fn, number=number, repeat=repeat)) / number
    print(f"| {name} | {best * 1e6:,.2f} | {1 / best:,.0f} |")


def header() -> None:
    print(f"Python {platform.python_version()} on {platform.machine()} ({sys.platform})\n")
    print("| case | µs/op | ops/s |")
    print("|---|---:|---:|")


KEY = b"bench-audit-key-not-a-secret-000"
SIGNING = "bench-signing-key-not-a-secret-0"
NOW = 1_767_225_600.0


def chain(rows: int) -> AuditChain:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE access_log (ts REAL NOT NULL, actor TEXT NOT NULL, user_id TEXT NOT NULL,"
        " purpose TEXT NOT NULL, fields TEXT NOT NULL, decision TEXT NOT NULL, request_id TEXT);"
    )
    c = AuditChain(db, KEY)
    for i in range(rows):
        c.append(NOW + i, "svc:api", f"u{i % 97}", "support", ["email", "name"], "allow", None)
    return c


def main() -> None:
    header()
    c = chain(0)
    n = iter(range(10**9))
    bench(
        "audit append (HMAC chain, SQLite in memory)",
        lambda: c.append(NOW + next(n), "svc:api", "u1", "support", ["email"], "allow", None),
        2000,
    )
    big = chain(10_000)
    bench("audit verify, 10,000-row chain", big.verify, 3)
    flags = Flags("prod", parse_flags({"f": {"owner": "me", "rollout": {"prod": 25}}}))
    bench("flag evaluate (25% rollout)", lambda: flags.evaluate("f", "user-42"), 50000)
    bench("rollout bucket hash", lambda: bucket("f", "user-42"), 50000)
    token = mint(SIGNING, "user-42", "support", now=NOW)
    bench(
        "bearer token authenticate",
        lambda: authenticate(f"Bearer {token}", SIGNING, now=NOW + 1),
        20000,
    )
    reg = Registry()
    reg.histogram("latency_seconds", "request latency")
    bench(
        "histogram observe",
        lambda: reg.observe("latency_seconds", 0.012, route="/x"),
        50000,
    )


if __name__ == "__main__":
    main()
