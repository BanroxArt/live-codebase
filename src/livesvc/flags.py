"""Feature flags with deterministic percentage rollout and kill switches.

A flag file is JSON:

    {
      "new_recs": {
        "owner": "growth",
        "remove_by": "2026-09-30",
        "default": false,
        "rollout": {"dev": 100, "staging": 100, "prod": 5},
        "allow": ["user-qa-1"],
        "deny": [],
        "killed": false
      }
    }

Evaluation order is fixed and every result says which rule decided it:
kill switch, unknown flag, deny list, allow list, percentage. Unknown flags
are off. A typo should never turn a feature on.

There are three ways to pull a kill switch, from most to least durable:
``"killed": true`` in the file (every process picks it up on its next
reload check), ``KILL_SWITCHES=a,b`` in the environment (at boot), and
``Flags.kill()`` / the admin endpoint (this process only, gone on restart).
With more than one process, use the file.

Bucketing hashes ``flag:unit_id`` with SHA-256 into 10,000 buckets. The same
user always lands in the same bucket for a given flag, and raising the
percentage only ever adds users, so nobody flips off when you go from 5% to
20%. Hashing the flag name in means different flags pick different 5%s.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
BUCKETS = 10_000


class FlagError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Flag:
    name: str
    owner: str
    remove_by: date | None
    default: bool = False
    rollout: dict[str, float] = field(default_factory=dict)
    allow: frozenset[str] = frozenset()
    deny: frozenset[str] = frozenset()
    killed: bool = False


@dataclass(frozen=True, slots=True)
class Evaluation:
    flag: str
    enabled: bool
    reason: str
    bucket: int | None = None


def bucket(flag: str, unit_id: str) -> int:
    digest = hashlib.sha256(f"{flag}:{unit_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def parse_flags(data: dict[str, Any]) -> dict[str, Flag]:
    if not isinstance(data, dict):
        raise FlagError("flag file must be an object")
    out = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            raise FlagError(f"{name}: must be an object")
        if not raw.get("owner"):
            # A flag without an owner is a flag nobody will remove.
            raise FlagError(f"{name}: owner is required")
        rollout = {}
        for env, pct in (raw.get("rollout") or {}).items():
            try:
                pct = float(pct)
            except (TypeError, ValueError) as exc:
                raise FlagError(f"{name}: rollout.{env} must be a number") from exc
            if not 0 <= pct <= 100:
                raise FlagError(f"{name}: rollout.{env} must be 0..100")
            rollout[str(env)] = pct
        remove_by = None
        if raw.get("remove_by"):
            try:
                remove_by = date.fromisoformat(str(raw["remove_by"]))
            except ValueError as exc:
                raise FlagError(f"{name}: remove_by must be YYYY-MM-DD") from exc
        out[name] = Flag(
            name=name,
            owner=str(raw["owner"]),
            remove_by=remove_by,
            default=bool(raw.get("default", False)),
            rollout=rollout,
            allow=frozenset(map(str, raw.get("allow", []))),
            deny=frozenset(map(str, raw.get("deny", []))),
            killed=bool(raw.get("killed", False)),
        )
    return out


class Flags:
    def __init__(
        self,
        env: str,
        flags: dict[str, Flag] | None = None,
        kill_switches: frozenset[str] | set[str] = frozenset(),
        path: str | Path | None = None,
        check_every_s: float = 5.0,
    ) -> None:
        self.env = env
        self.check_every_s = check_every_s
        self._next_check = 0.0
        self._flags = dict(flags or {})
        self._killed: set[str] = set(kill_switches)
        self._lock = threading.Lock()
        self._path = Path(path) if path else None
        self._mtime: float | None = None
        if self._path:
            self.reload()

    @property
    def loaded(self) -> bool:
        return self._path is None or self._mtime is not None

    def reload(self) -> bool:
        """Re-read the flag file if it changed. A bad file keeps the last good set."""
        if not self._path:
            return False
        self._next_check = time.monotonic() + self.check_every_s
        try:
            mtime = os.stat(self._path).st_mtime
        except OSError as exc:
            log.error("flag file unreadable", extra={"path": str(self._path), "error": str(exc)})
            return False
        if mtime == self._mtime:
            return False
        try:
            flags = parse_flags(json.loads(self._path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            log.error("flag file rejected, keeping previous flags", extra={"error": str(exc)})
            return False
        with self._lock:
            self._flags = flags
            self._mtime = mtime
        return True

    def kill(self, name: str) -> None:
        with self._lock:
            self._killed.add(name)
        log.warning("kill switch on", extra={"flag": name})

    def revive(self, name: str) -> None:
        with self._lock:
            self._killed.discard(name)
        log.warning("kill switch off", extra={"flag": name})

    def killed(self) -> set[str]:
        with self._lock:
            return set(self._killed)

    def _maybe_reload(self) -> None:
        # Cheap stat at most every check_every_s; keeps every worker in step
        # with the file without a watcher thread.
        if self._path and time.monotonic() >= self._next_check:
            self.reload()

    def evaluate(self, name: str, unit_id: str | None) -> Evaluation:
        self._maybe_reload()
        with self._lock:
            flag = self._flags.get(name)
            killed = name in self._killed or "*" in self._killed or bool(flag and flag.killed)
        if killed:
            return Evaluation(name, False, "killed")
        if flag is None:
            return Evaluation(name, False, "unknown_flag")
        if unit_id is not None and unit_id in flag.deny:
            return Evaluation(name, False, "deny_list")
        if unit_id is not None and unit_id in flag.allow:
            return Evaluation(name, True, "allow_list")
        pct = flag.rollout.get(self.env)
        if pct is None:
            return Evaluation(name, flag.default, "default")
        if unit_id is None:
            # No stable id means no stable bucket; fall back rather than flap.
            return Evaluation(name, pct >= 100, "no_unit_id")
        b = bucket(name, unit_id)
        return Evaluation(name, b < pct * BUCKETS / 100, "rollout", b)

    def enabled(self, name: str, unit_id: str | None = None) -> bool:
        return self.evaluate(name, unit_id).enabled

    def stale(self, today: date | None = None) -> list[Flag]:
        today = today or date.today()
        with self._lock:
            flags = list(self._flags.values())
        return [f for f in flags if f.remove_by and f.remove_by < today]

    def snapshot(self) -> dict[str, Any]:
        # Copy under the lock; a reload swaps both while we would be iterating.
        with self._lock:
            flags = dict(self._flags)
            killed = set(self._killed)
        return {
            "env": self.env,
            "killed": sorted(killed | {n for n, f in flags.items() if f.killed}),
            "flags": {
                n: {
                    "owner": f.owner,
                    "rollout": f.rollout.get(self.env),
                    "remove_by": str(f.remove_by or ""),
                }
                for n, f in sorted(flags.items())
            },
        }
