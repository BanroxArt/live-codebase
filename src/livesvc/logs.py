"""Structured JSON logging with request context and redaction.

Every line carries ``request_id`` and ``trace_id`` when there is a request in
flight, so one id from a support ticket finds every log line for it.
Redaction runs on the structured fields and on the message; it is a safety
net, not permission to log sensitive data on purpose.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

SENSITIVE_KEYS = re.compile(
    r"(?i)(pass(word)?|secret|token|authorization|cookie|api[_-]?key|ssn|card|cvv|iban|account_number)"
)
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# 13 to 19 digits with optional spaces or dashes: card-number shaped.
PAN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def scrub_text(text: str) -> str:
    text = BEARER.sub("Bearer [redacted]", text)
    text = EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)

    def pan(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[card]" if _luhn_ok(digits) else m.group(0)

    return PAN.sub(pan, text)


def scrub(value: Any, key: str = "") -> Any:
    if key and SENSITIVE_KEYS.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def __init__(
        self, service: str = "livesvc", env: str = "dev", release: str = "unknown"
    ) -> None:
        super().__init__()
        self.static = {"service": service, "env": env, "release": release}

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": scrub_text(record.getMessage()),
            **self.static,
        }
        if (rid := request_id.get()) is not None:
            doc["request_id"] = rid
        if (tid := trace_id.get()) is not None:
            doc["trace_id"] = tid
        extra = {
            k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")
        }
        if extra:
            doc.update(scrub(extra))
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, separators=(",", ":"))


def configure(
    level: str = "INFO",
    service: str = "livesvc",
    env: str = "dev",
    release: str = "unknown",
    stream: Any = None,
) -> logging.Logger:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service, env, release))
    root.addHandler(handler)
    root.setLevel(level)
    return root
