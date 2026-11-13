"""Tracing with W3C ``traceparent`` propagation.

The built-in tracer parses and emits ``traceparent``, keeps the trace id in
the log context and records finished spans in memory, so logs from one
request share an id with whatever called it.

If ``opentelemetry-api`` is installed (``pip install livesvc[otel]``), each
span is mirrored into an OTel span so a configured exporter picks it up. The
mirrored spans use OTel's own context; wiring the incoming header into OTel
is the job of the OTel propagator and is not done here. Code that calls
``tracer.span(...)`` does not change either way.
"""

from __future__ import annotations

import contextlib
import contextvars
import random
import re
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from livesvc import logs

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("span", default=None)


@dataclass(frozen=True, slots=True)
class SpanContext:
    trace_id: str
    span_id: str
    sampled: bool = True

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


def parse_traceparent(header: str | None) -> SpanContext | None:
    if not header:
        return None
    m = TRACEPARENT.match(header.strip().lower())
    if not m:
        return None
    trace, span, flags = m.groups()
    if trace == "0" * 32 or span == "0" * 16:
        return None
    return SpanContext(trace, span, bool(int(flags, 16) & 1))


def _hex(nbytes: int) -> str:
    return f"{random.getrandbits(nbytes * 8):0{nbytes * 2}x}"


@dataclass(slots=True)
class Span:
    name: str
    context: SpanContext
    parent_id: str | None
    start: float = field(default_factory=time.perf_counter)
    end: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    @property
    def duration_ms(self) -> float:
        return ((self.end or time.perf_counter()) - self.start) * 1000


class Tracer:
    """In-process tracer.

    ``trust_parent`` controls whether an inbound ``traceparent`` decides
    sampling. The trace id is always continued so logs line up with the
    caller; the sampled bit is only honoured when the caller is trusted
    (another service of yours behind the same edge). Otherwise any client can
    send ``-01`` and force every request to be recorded.
    """

    def __init__(
        self, sample_rate: float = 1.0, keep: int = 1000, trust_parent: bool = False
    ) -> None:
        self.sample_rate = sample_rate
        self.trust_parent = trust_parent
        self.finished: deque[Span] = deque(maxlen=keep)

    def current(self) -> Span | None:
        return _current.get()

    @contextlib.contextmanager
    def span(self, name: str, parent: SpanContext | None = None, **attrs: Any) -> Iterator[Span]:
        active = _current.get()
        remote = parent is not None
        parent = parent or (active.context if active else None)
        if parent:
            sampled = parent.sampled
            if remote and not self.trust_parent:
                sampled = random.random() < self.sample_rate
            ctx = SpanContext(parent.trace_id, _hex(8), sampled)
        else:
            ctx = SpanContext(_hex(16), _hex(8), random.random() < self.sample_rate)
        sp = Span(name, ctx, parent.span_id if parent else None, attributes=dict(attrs))
        token = _current.set(sp)
        trace_token = logs.trace_id.set(ctx.trace_id)
        try:
            yield sp
        except BaseException:
            sp.status = "error"
            raise
        finally:
            sp.end = time.perf_counter()
            _current.reset(token)
            logs.trace_id.reset(trace_token)
            if ctx.sampled:
                self.finished.append(sp)


class OtelTracer(Tracer):
    """Same API, backed by opentelemetry-api."""

    def __init__(self, sample_rate: float = 1.0, trust_parent: bool = False) -> None:
        super().__init__(sample_rate, trust_parent=trust_parent)
        from opentelemetry import trace

        self._otel = trace.get_tracer("livesvc")

    @contextlib.contextmanager
    def span(self, name: str, parent: SpanContext | None = None, **attrs: Any) -> Iterator[Span]:
        with super().span(name, parent, **attrs) as sp, self._otel.start_as_current_span(name) as o:
            for k, v in attrs.items():
                o.set_attribute(k, v)
            yield sp


def make_tracer(sample_rate: float = 1.0, trust_parent: bool = False) -> Tracer:
    try:
        return OtelTracer(sample_rate, trust_parent=trust_parent)
    except ImportError:
        return Tracer(sample_rate, trust_parent=trust_parent)
