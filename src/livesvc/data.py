"""Consent-aware data access.

Application code asks for fields *for a purpose*. The layer checks the
purpose is allowed for those fields, checks the user's consent for purposes
that need it, masks or drops what is not allowed, and writes an audit row for
every read, allowed or not.

It also stamps each database with the environment that created it and
refuses to open a database from another environment. Pointing a dev box at a
prod copy is the most common way real personal data ends up somewhere it
should not be, and a config typo should fail loudly rather than work.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

# purpose -> fields it may read. "service" is running the product for the
# user; it does not need opt-in. Anything else does.
PURPOSE_FIELDS: dict[str, frozenset[str]] = {
    "service": frozenset({"id", "display_name", "email", "plan", "created_at"}),
    "support": frozenset({"id", "display_name", "email", "plan", "created_at"}),
    "analytics": frozenset({"id", "plan", "created_at"}),
    "marketing": frozenset({"id", "display_name", "email"}),
}
CONSENT_REQUIRED = frozenset({"analytics", "marketing"})
MASKED = {"email"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS consents (
  user_id TEXT NOT NULL REFERENCES users(id),
  purpose TEXT NOT NULL,
  version TEXT NOT NULL,
  granted_at REAL NOT NULL,
  revoked_at REAL,
  PRIMARY KEY (user_id, purpose, granted_at)
);
CREATE TABLE IF NOT EXISTS access_log (
  ts REAL NOT NULL,
  actor TEXT NOT NULL,
  user_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  fields TEXT NOT NULL,
  decision TEXT NOT NULL,
  request_id TEXT
);
"""


class EnvironmentMismatch(RuntimeError):
    pass


class PurposeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReadResult:
    data: dict[str, object]
    withheld: tuple[str, ...]
    decision: str


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class Store:
    def __init__(self, path: str, env: str) -> None:
        self.env = env
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT value FROM meta WHERE key = 'env'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO meta (key, value) VALUES ('env', ?)", (env,))
        elif row[0] != env:
            self.db.close()
            raise EnvironmentMismatch(f"database belongs to {row[0]!r}, this process is {env!r}")

    def ping(self) -> None:
        self.db.execute("SELECT 1").fetchone()

    def add_user(self, user_id: str, display_name: str, email: str, plan: str = "free") -> None:
        self.db.execute(
            "INSERT INTO users (id, display_name, email, plan, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, display_name, email, plan, time.time()),
        )

    def grant(self, user_id: str, purpose: str, version: str, at: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO consents (user_id, purpose, version, granted_at) VALUES (?, ?, ?, ?)",
            (user_id, purpose, version, at if at is not None else time.time()),
        )

    def revoke(self, user_id: str, purpose: str, at: float | None = None) -> int:
        cur = self.db.execute(
            "UPDATE consents SET revoked_at = ? WHERE user_id = ? AND purpose = ? AND revoked_at IS NULL",
            (at if at is not None else time.time(), user_id, purpose),
        )
        return cur.rowcount

    def has_consent(self, user_id: str, purpose: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM consents WHERE user_id = ? AND purpose = ? AND revoked_at IS NULL LIMIT 1",
            (user_id, purpose),
        ).fetchone()
        return row is not None

    def _audit(
        self,
        actor: str,
        user_id: str,
        purpose: str,
        fields: Iterable[str],
        decision: str,
        request_id: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO access_log (ts, actor, user_id, purpose, fields, decision, request_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), actor, user_id, purpose, ",".join(sorted(fields)), decision, request_id),
        )

    def read(
        self,
        user_id: str,
        purpose: str,
        fields: Iterable[str],
        *,
        actor: str,
        request_id: str | None = None,
    ) -> ReadResult | None:
        # One connection shared across request threads; serialise reads so the
        # audit row and the data read cannot interleave with another request.
        with self._lock:
            return self._read(user_id, purpose, fields, actor, request_id)

    def _read(
        self, user_id: str, purpose: str, fields: Iterable[str], actor: str, request_id: str | None
    ) -> ReadResult | None:
        fields = tuple(dict.fromkeys(fields))
        if purpose not in PURPOSE_FIELDS:
            raise PurposeError(f"unknown purpose {purpose!r}")
        allowed = PURPOSE_FIELDS[purpose]
        unknown = [f for f in fields if f not in PURPOSE_FIELDS["service"]]
        if unknown:
            raise PurposeError(f"unknown fields {unknown}")
        if purpose in CONSENT_REQUIRED and not self.has_consent(user_id, purpose):
            self._audit(actor, user_id, purpose, fields, "denied_no_consent", request_id)
            return ReadResult({}, fields, "denied_no_consent")
        permitted = [f for f in fields if f in allowed]
        withheld = tuple(f for f in fields if f not in allowed)
        if not permitted:
            self._audit(actor, user_id, purpose, fields, "denied_purpose", request_id)
            return ReadResult({}, withheld, "denied_purpose")
        cols = ", ".join(permitted)  # safe: every name was checked against a fixed set above
        row = self.db.execute(f"SELECT {cols} FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            self._audit(actor, user_id, purpose, fields, "not_found", request_id)
            return None
        data = dict(zip(permitted, row, strict=True))
        if purpose == "support":
            for f in MASKED & data.keys():
                data[f] = mask_email(str(data[f]))
        decision = "allowed" if not withheld else "partial"
        self._audit(actor, user_id, purpose, fields, decision, request_id)
        return ReadResult(data, withheld, decision)

    def access_log(self, user_id: str) -> list[tuple]:
        return self.db.execute(
            "SELECT actor, purpose, fields, decision FROM access_log WHERE user_id = ? ORDER BY ts",
            (user_id,),
        ).fetchall()

    def close(self) -> None:
        self.db.close()
