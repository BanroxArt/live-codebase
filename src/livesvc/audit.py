"""Tamper-evident access log: a hash chain over ``access_log`` rows.

Each row stores ``seq``, the previous row's hash and its own hash::

    row_hash = HMAC-SHA256(key, canonical(seq, prev_hash, ts, actor, user_id,
                                          purpose, fields, decision, request_id))

Editing, inserting or deleting a row in the middle breaks every hash after it,
and ``verify`` names the first row that does not match. What this does and
does not protect against:

- Someone who can write the database but does not hold the key cannot
  rewrite a row and recompute the chain. Without a key (``AUDIT_CHAIN_KEY``
  unset) the chain is plain SHA-256: it still catches accidental edits and
  careless tampering, but anyone with write access can recompute it.
- Cutting rows off the end leaves a valid, shorter chain. The defence is to
  copy ``head()`` somewhere the database owner cannot write (a log pipeline,
  object storage with retention lock, a ticket) and pass it back to
  ``verify(expected=...)``. The admin listener serves it at
  ``/admin/audit/head``.
- Triggers refuse ``UPDATE`` and ``DELETE`` on the table. They stop bugs and
  casual edits, not a person with shell access, who can drop a trigger. The
  hash chain is what catches that person.

Retention: ``prune(before_ts)`` deletes the oldest rows and records the last
deleted row's ``(seq, hash)`` as the new anchor, so the remaining chain still
verifies from a known point. It is the only path the delete trigger lets
through.

Concurrency: ``append`` runs in ``BEGIN IMMEDIATE``, which takes SQLite's
write lock before reading the head, so two processes on one file cannot both
extend the same head. ``prev_hash`` is also unique, which turns a fork into a
constraint error instead of a silent second branch.

The chain mode and a key id are written to ``meta`` when the chain is first
created. Opening it later with a different key, or without one, is refused:
mixing modes would make ``verify`` fail for reasons that look like tampering.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass

GENESIS = "0" * 64
CHAIN_VERSION = "v1"
KEY_ENV = "AUDIT_CHAIN_KEY"
MIN_KEY_LEN = 32

_COLUMNS = ("seq", "prev_hash", "row_hash")
_SELECT = (
    "SELECT seq, prev_hash, row_hash, ts, actor, user_id, purpose, fields, decision, request_id"
    " FROM access_log WHERE seq IS NOT NULL"
)

TRIGGERS = """
CREATE UNIQUE INDEX IF NOT EXISTS access_log_seq ON access_log(seq);
CREATE UNIQUE INDEX IF NOT EXISTS access_log_prev ON access_log(prev_hash);
CREATE TRIGGER IF NOT EXISTS access_log_no_update BEFORE UPDATE ON access_log
BEGIN
  SELECT RAISE(ABORT, 'access_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS access_log_no_delete BEFORE DELETE ON access_log
WHEN (SELECT value FROM meta WHERE key = 'audit_pruning') IS NOT '1'
BEGIN
  SELECT RAISE(ABORT, 'access_log is append-only; use prune');
END;
"""


class ChainConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Head:
    seq: int
    hash: str

    def __str__(self) -> str:
        return f"{self.seq}:{self.hash}"

    @classmethod
    def parse(cls, text: str) -> Head:
        seq, sep, digest = text.strip().partition(":")
        if not sep or not seq.isdigit() or len(digest) != 64:
            raise ValueError("expected SEQ:HASH with a 64 character hex hash")
        int(digest, 16)
        return cls(int(seq), digest.lower())


@dataclass(frozen=True, slots=True)
class Verification:
    ok: bool
    checked: int
    head: Head
    anchor: Head
    unchained: int = 0
    first_bad_seq: int | None = None
    reason: str = ""


def _key_id(key: bytes | None) -> str:
    if key is None:
        return "none"
    return hmac.new(key, b"livesvc audit key id", hashlib.sha256).hexdigest()[:16]


def canonical(
    seq: int,
    prev_hash: str,
    ts: float,
    actor: str,
    user_id: str,
    purpose: str,
    fields: str,
    decision: str,
    request_id: str | None,
) -> bytes:
    """Stable byte encoding of a row. JSON floats round-trip exactly through SQLite REAL."""
    doc = [CHAIN_VERSION, seq, prev_hash, ts, actor, user_id, purpose, fields, decision, request_id]
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class AuditChain:
    def __init__(
        self, db: sqlite3.Connection, key: bytes | None = None, *, readonly: bool = False
    ) -> None:
        if key is not None and len(key) < MIN_KEY_LEN:
            raise ChainConfigError(f"{KEY_ENV} must be at least {MIN_KEY_LEN} bytes")
        self.db = db
        self._key = key
        if readonly:
            if self._meta("audit_chain_mode") is None:
                raise ChainConfigError("this database has no audit chain")
        else:
            self._migrate()
        self._check_mode()

    def __repr__(self) -> str:  # never print the key
        return f"AuditChain(keyed={self.keyed})"

    @property
    def keyed(self) -> bool:
        return self._key is not None

    def _digest(self, payload: bytes) -> str:
        if self._key is None:
            return hashlib.sha256(payload).hexdigest()
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _migrate(self) -> None:
        have = {row[1] for row in self.db.execute("PRAGMA table_info(access_log)")}
        for col in _COLUMNS:
            if col not in have:
                # Rows written before the chain existed keep NULL here and are
                # reported as unchained by verify().
                kind = "INTEGER" if col == "seq" else "TEXT"
                self.db.execute(f"ALTER TABLE access_log ADD COLUMN {col} {kind}")
        self.db.executescript(TRIGGERS)
        if self._meta("audit_chain_mode") is None:
            self._set_meta("audit_chain_mode", self._mode)
            self._set_meta("audit_key_id", _key_id(self._key))

    @property
    def _mode(self) -> str:
        return "hmac-sha256" if self.keyed else "sha256"

    def _check_mode(self) -> None:
        stored_mode, stored_id = self._meta("audit_chain_mode"), self._meta("audit_key_id")
        mode = self._mode
        if stored_mode != mode or stored_id != _key_id(self._key):
            raise ChainConfigError(
                f"audit chain was created as {stored_mode} with key id {stored_id};"
                f" this process has {mode} with key id {_key_id(self._key)}"
            )

    def anchor(self) -> Head:
        raw = self._meta("audit_anchor")
        return Head.parse(raw) if raw else Head(0, GENESIS)

    def head(self) -> Head:
        row = self.db.execute(
            "SELECT seq, row_hash FROM access_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return Head(int(row[0]), str(row[1])) if row else self.anchor()

    def append(
        self,
        ts: float,
        actor: str,
        user_id: str,
        purpose: str,
        fields: Iterable[str],
        decision: str,
        request_id: str | None,
    ) -> Head:
        joined = ",".join(sorted(fields))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prev = self.head()
            seq = prev.seq + 1
            digest = self._digest(
                canonical(seq, prev.hash, ts, actor, user_id, purpose, joined, decision, request_id)
            )
            self.db.execute(
                "INSERT INTO access_log (seq, prev_hash, row_hash, ts, actor, user_id, purpose,"
                " fields, decision, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, prev.hash, digest, ts, actor, user_id, purpose, joined, decision, request_id),
            )
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return Head(seq, digest)

    def verify(self, expected: Head | None = None) -> Verification:
        """Walk the chain from the anchor. Stops at the first row that does not match."""
        anchor = self.anchor()
        unchained = int(
            self.db.execute("SELECT count(*) FROM access_log WHERE seq IS NULL").fetchone()[0]
        )
        prev = anchor
        checked = 0
        at_checkpoint: str | None = None

        def fail(seq: int | None, reason: str) -> Verification:
            return Verification(False, checked, prev, anchor, unchained, seq, reason)

        for row in self.db.execute(_SELECT + " ORDER BY seq"):
            seq, prev_hash, row_hash = int(row[0]), str(row[1]), str(row[2])
            if seq != prev.seq + 1:
                return fail(prev.seq + 1, f"gap: expected seq {prev.seq + 1}, found {seq}")
            if not hmac.compare_digest(prev_hash, prev.hash):
                return fail(seq, "prev_hash does not match the previous row")
            want = self._digest(canonical(seq, prev_hash, *row[3:]))
            if not hmac.compare_digest(want, row_hash):
                return fail(seq, "row contents do not match row_hash")
            prev = Head(seq, row_hash)
            checked += 1
            if expected is not None and seq == expected.seq:
                at_checkpoint = row_hash
        if expected is not None:
            if expected.seq < anchor.seq:
                reason = f"checkpoint {expected.seq} is older than the prune anchor {anchor.seq}"
                return Verification(True, checked, prev, anchor, unchained, None, reason)
            got = anchor.hash if expected.seq == anchor.seq else at_checkpoint
            if got is None:
                return fail(
                    expected.seq, f"chain ends at {prev.seq}, checkpoint was {expected.seq}"
                )
            if not hmac.compare_digest(got, expected.hash):
                return fail(expected.seq, "row at checkpoint has a different hash")
        return Verification(True, checked, prev, anchor, unchained)

    def prune(self, before_ts: float) -> int:
        """Delete the oldest chained rows with ``ts < before_ts``; keep the chain verifiable.

        Only a prefix is removed: the first row at or after ``before_ts`` stops
        the cut even if later rows are older, so the remaining rows stay
        contiguous.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            last: Head | None = None
            for seq, row_hash, ts in self.db.execute(
                "SELECT seq, row_hash, ts FROM access_log WHERE seq IS NOT NULL ORDER BY seq"
            ):
                if ts >= before_ts:
                    break
                last = Head(int(seq), str(row_hash))
            if last is None:
                self.db.execute("COMMIT")
                return 0
            self._set_meta("audit_pruning", "1")
            cur = self.db.execute("DELETE FROM access_log WHERE seq <= ?", (last.seq,))
            self.db.execute("DELETE FROM meta WHERE key = 'audit_pruning'")
            self._set_meta("audit_anchor", str(last))
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return int(cur.rowcount)


def key_from_env(env: dict[str, str] | None = None) -> bytes | None:
    raw = (os.environ if env is None else env).get(KEY_ENV) or ""
    return raw.encode("utf-8") if raw else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m livesvc.audit", description="Verify the access log hash chain."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help=f"walk the chain; reads {KEY_ENV} from the environment")
    v.add_argument("database")
    v.add_argument("--expect", metavar="SEQ:HASH", help="a head recorded earlier, elsewhere")
    h = sub.add_parser("head", help="print SEQ:HASH for the newest row")
    h.add_argument("database")
    args = p.parse_args(argv)
    if not os.path.exists(args.database):
        print(f"no such database: {args.database}", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True, isolation_level=None)
    try:
        chain = AuditChain(db, key_from_env(), readonly=True)
        if args.cmd == "head":
            print(chain.head())
            return 0
        expected = Head.parse(args.expect) if args.expect else None
        result = chain.verify(expected)
    except (ChainConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        db.close()
    status = "ok" if result.ok else "TAMPERED"
    print(f"{status}: {result.checked} row(s) checked, head {result.head}, anchor {result.anchor}")
    if result.unchained:
        print(f"{result.unchained} row(s) predate the chain and are not covered")
    if result.reason:
        where = f"first bad row {result.first_bad_seq}: " if not result.ok else ""
        print(where + result.reason)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
