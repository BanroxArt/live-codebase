"""Caller identity for the user-facing API.

The server, not the client, decides who is asking and what they may read.
A request carries a bearer token; the token names a subject and a role, and
the role fixes the purpose and the field list for profile reads. Nothing in
the query string or in client headers can widen that.

This is a stub with the right shape, not an identity provider. Tokens are
``base64url(payload).base64url(hmac_sha256(payload))`` signed with
``AUTH_SIGNING_KEY``. In a real deployment the gateway or your OIDC library
verifies the caller and this module maps the verified claims to a
``Principal``; ``authenticate`` is the one function to replace.

Role policy (``ROLES``):

- ``user``: reads only their own profile, purpose ``service``.
- ``support``: any profile, purpose ``support`` (email masked by the data layer).
- ``analytics``: any profile, purpose ``analytics`` (consent required, no email).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass

MIN_KEY_LEN = 32
MAX_TOKEN_LEN = 2048


@dataclass(frozen=True, slots=True)
class RolePolicy:
    purpose: str
    fields: tuple[str, ...]
    own_records_only: bool


ROLES: Mapping[str, RolePolicy] = {
    "user": RolePolicy("service", ("id", "display_name", "email", "plan"), own_records_only=True),
    "support": RolePolicy(
        "support", ("id", "display_name", "email", "plan", "created_at"), own_records_only=False
    ),
    "analytics": RolePolicy("analytics", ("id", "plan", "created_at"), own_records_only=False),
}


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: str

    @property
    def policy(self) -> RolePolicy:
        return ROLES[self.role]

    @property
    def actor(self) -> str:
        """What the audit log records: role and subject, both from the token."""
        return f"{self.role}:{self.subject}"


class AuthError(Exception):
    """Token missing, malformed, expired or not signed by us. Maps to 401."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, payload: str) -> str:
    return _b64e(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def mint(key: str, subject: str, role: str, ttl_s: float = 3600, now: float | None = None) -> str:
    """Issue a token. For tests and local runs; production tokens come from your IdP."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if len(key) < MIN_KEY_LEN:
        raise ValueError(f"signing key must be at least {MIN_KEY_LEN} characters")
    exp = int((time.time() if now is None else now) + ttl_s)
    payload = _b64e(json.dumps({"sub": subject, "role": role, "exp": exp}).encode())
    return f"{payload}.{_sign(key.encode(), payload)}"


def authenticate(authorization: str | None, key: str | None, now: float | None = None) -> Principal:
    if not key:
        raise AuthError("authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = authorization[7:].strip()
    if len(token) > MAX_TOKEN_LEN or token.count(".") != 1:
        raise AuthError("malformed token")
    payload, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(key.encode(), payload)):
        raise AuthError("bad signature")
    try:
        claims = json.loads(_b64d(payload))
    except (ValueError, binascii.Error) as exc:
        raise AuthError("malformed token") from exc
    if not isinstance(claims, dict):
        raise AuthError("malformed token")
    sub, role, exp = claims.get("sub"), claims.get("role"), claims.get("exp")
    if not isinstance(sub, str) or not sub or role not in ROLES or not isinstance(exp, int):
        raise AuthError("malformed token")
    if exp <= (time.time() if now is None else now):
        raise AuthError("token expired")
    return Principal(sub, str(role))


def main(argv: list[str] | None = None) -> int:
    """``python -m livesvc.auth SUBJECT ROLE``: print a one-hour dev token."""
    import os
    import sys

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m livesvc.auth SUBJECT ROLE", file=sys.stderr)
        return 2
    key = os.environ.get("AUTH_SIGNING_KEY", "")
    try:
        print(mint(key, args[0], args[1]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
