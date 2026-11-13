import pytest

from livesvc import logs
from livesvc.tracing import SpanContext, Tracer, parse_traceparent

TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_parse_traceparent():
    ctx = parse_traceparent(TP)
    assert ctx == SpanContext("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7", True)
    assert parse_traceparent(TP[:-2] + "00").sampled is False


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "garbage",
        "01-" + TP[3:],
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01",
    ],
)
def test_invalid_traceparent(bad):
    assert parse_traceparent(bad) is None


def test_child_spans_share_trace_and_link_parent():
    t = Tracer()
    with t.span("outer", parent=parse_traceparent(TP)) as outer, t.span("inner") as inner:
        assert logs.trace_id.get() == outer.context.trace_id
    assert outer.context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert outer.parent_id == "00f067aa0ba902b7"
    assert inner.parent_id == outer.context.span_id
    assert [s.name for s in t.finished] == ["inner", "outer"]
    assert logs.trace_id.get() is None


def test_errors_mark_span_and_propagate():
    t = Tracer()
    with pytest.raises(RuntimeError), t.span("x"):
        raise RuntimeError("no")
    assert t.finished[-1].status == "error"


def test_unsampled_spans_not_recorded():
    t = Tracer(sample_rate=0.0)
    with t.span("x") as sp:
        pass
    assert not sp.context.sampled
    assert len(t.finished) == 0


def test_traceparent_round_trip():
    ctx = SpanContext("a" * 32, "b" * 16, True)
    assert parse_traceparent(ctx.traceparent()) == ctx


def test_untrusted_parent_cannot_force_sampling():
    # Regression: the inbound sampled bit was always honoured, so any client
    # could send -01 and have every request recorded regardless of the rate.
    t = Tracer(sample_rate=0.0)
    with t.span("req", parent=parse_traceparent(TP)) as sp:
        assert sp.context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert sp.context.sampled is False
    assert list(t.finished) == []


def test_trusted_parent_decides_sampling():
    t = Tracer(sample_rate=0.0, trust_parent=True)
    with t.span("req", parent=parse_traceparent(TP)) as sp:
        assert sp.context.sampled is True
    with t.span("req", parent=parse_traceparent(TP[:-2] + "00")) as sp:
        assert sp.context.sampled is False


def test_children_inherit_local_decision():
    t = Tracer(sample_rate=0.0)
    with t.span("outer", parent=parse_traceparent(TP)), t.span("inner") as inner:
        assert inner.context.sampled is False
