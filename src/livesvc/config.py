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
    database_path: str = ":memory:"
    flags_path: str | None = None
    kill_switches: frozenset[str] = field(default_factory=frozenset)
    admin_token: str | None = field(default=None, repr=False)
    drain_seconds: float = 10.0
    trace_sample_rate: float = 1.0

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


def load(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    app_env = env.get("APP_ENV", "dev")
    if app_env not in ENVIRONMENTS:
        raise ConfigError(f"APP_ENV must be one of {', '.join(ENVIRONMENTS)}")
    token = env.get("ADMIN_TOKEN") or None
    if app_env == "prod" and token is not None and len(token) < 32:
        raise ConfigError("ADMIN_TOKEN is too short for prod (32+ chars)")
    kills = frozenset(k.strip() for k in env.get("KILL_SWITCHES", "").split(",") if k.strip())
    return Settings(
        env=app_env,
        service=env.get("SERVICE_NAME", "livesvc"),
        release=env.get("RELEASE", env.get("GIT_SHA", "unknown")),
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
        host=env.get("HOST", "127.0.0.1"),
        port=int(_float(env, "PORT", 8080, 1, 65535)),
        database_path=env.get("DATABASE_PATH", ":memory:"),
        flags_path=env.get("FLAGS_PATH") or None,
        kill_switches=kills,
        admin_token=token,
        drain_seconds=_float(env, "DRAIN_SECONDS", 10.0, 0, 300),
        trace_sample_rate=_float(env, "TRACE_SAMPLE_RATE", 1.0, 0, 1),
    )
