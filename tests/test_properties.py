"""Property tests for the pieces that parse or transform untrusted input."""

from hypothesis import given
from hypothesis import strategies as st

from livesvc import logs
from livesvc.flags import BUCKETS, bucket
from livesvc.tracing import parse_traceparent

hexstr = st.text(alphabet="0123456789abcdef", min_size=1, max_size=40)


@given(st.text(max_size=40), st.text(max_size=40))
def test_bucket_is_stable_and_in_range(flag, unit):
    b = bucket(flag, unit)
    assert 0 <= b < BUCKETS and b == bucket(flag, unit)


@given(st.text(max_size=120))
def test_traceparent_never_raises(header):
    ctx = parse_traceparent(header)
    if ctx is not None:
        assert len(ctx.trace_id) == 32 and len(ctx.span_id) == 16


@given(
    st.text(alphabet="0123456789abcdef", min_size=32, max_size=32),
    st.text(alphabet="0123456789abcdef", min_size=16, max_size=16),
    st.booleans(),
)
def test_traceparent_round_trip(trace, span, sampled):
    ctx = parse_traceparent(f"00-{trace}-{span}-{'01' if sampled else '00'}")
    if trace == "0" * 32 or span == "0" * 16:
        assert ctx is None
    else:
        assert ctx is not None and ctx.traceparent().endswith(("-01", "-00"))
        assert parse_traceparent(ctx.traceparent()) == ctx


@given(st.text(max_size=200))
def test_scrub_text_is_idempotent(text):
    once = logs.scrub_text(text)
    assert logs.scrub_text(once) == once


@given(
    st.recursive(
        st.text(max_size=20), lambda c: st.dictionaries(st.text(max_size=12), c, max_size=4)
    )
)
def test_scrub_never_leaks_sensitive_keys(value):
    def check(v):
        if isinstance(v, dict):
            for k, inner in v.items():
                if logs.sensitive_key(str(k)):
                    assert inner == "[redacted]"
                else:
                    check(inner)

    check(logs.scrub({"root": value}))
