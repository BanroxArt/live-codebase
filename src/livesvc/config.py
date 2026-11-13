"""Settings, from environment variables only.

Nothing is read from files in the repo, so the same image runs in every
environment and secrets never end up in git.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ENVIRONMENTS = ("dev", "test", "staging", "prod")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    env: str = "dev"
    service: str = "livesvc"
    release: str = "unknown"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8080
    # Metrics, version and admin endpoints listen here, not on the public port.
    admin_host: str = "127.0.0.1"
    admin_port: int = 9090
    database_path: str = ":memory:"
    flags_path: str | None = None
    kill_switches: frozenset[str] = field(default_factory=frozenset)
    admin_token: str | None = field(default=None, repr=False)
    auth_signing_key: str | None = field(default=None, repr=False)
    drain_seconds: float = 10.0
    trace_sample_rate: float = 1.0
    # Honour the caller's "sampled" bit. Off by default: on a public edge it
    # lets any client force full recording.
    trace_trust_parent: bool = False

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


def _float(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be within {lo}..{hi}")
    return value


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be within {lo}..{hi}")
    return value


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} must be true or false")


def load(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    app_env = env.get("APP_ENV", "dev")
    if app_env not in ENVIRONMENTS:
        raise ConfigError(f"APP_ENV must be one of {', '.join(ENVIRONMENTS)}")
    token = env.get("ADMIN_TOKEN") or None
    if app_env == "prod" and token is not None and len(token) < 32:
        raise ConfigError("ADMIN_TOKEN is too short for prod (32+ chars)")
    signing_key = env.get("AUTH_SIGNING_KEY") or None
    if signing_key is not None and len(signing_key) < 32:
        raise ConfigError("AUTH_SIGNING_KEY must be at least 32 characters")
    if app_env == "prod" and signing_key is None:
        # Fail closed: prod without caller authentication would serve
        # profile data to anyone who can reach the port.
        raise ConfigError("AUTH_SIGNING_KEY is required in prod")
    port = _int(env, "PORT", 8080, 1, 65535)
    admin_port = _int(env, "ADMIN_PORT", 9090, 1, 65535)
    if admin_port == port:
        raise ConfigError("ADMIN_PORT must differ from PORT")
    kills = frozenset(k.strip() for k in env.get("KILL_SWITCHES", "").split(",") if k.strip())
    return Settings(
        env=app_env,
        service=env.get("SERVICE_NAME", "livesvc"),
        release=env.get("RELEASE", env.get("GIT_SHA", "unknown")),
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
        host=env.get("HOST", "127.0.0.1"),
        port=port,
        admin_host=env.get("ADMIN_HOST", "127.0.0.1"),
        admin_port=admin_port,
        database_path=env.get("DATABASE_PATH", ":memory:"),
        flags_path=env.get("FLAGS_PATH") or None,
        kill_switches=kills,
        admin_token=token,
        auth_signing_key=signing_key,
        drain_seconds=_float(env, "DRAIN_SECONDS", 10.0, 0, 300),
        trace_sample_rate=_float(env, "TRACE_SAMPLE_RATE", 1.0, 0, 1),
        trace_trust_parent=_bool(env, "TRACE_TRUST_PARENT", False),
    )
