import io
import json
import logging

import pytest

from livesvc import logs


def capture():
    buf = io.StringIO()
    logs.configure("DEBUG", "svc", "test", "r1", stream=buf)
    return buf, logging.getLogger("t")


def lines(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_json_lines_with_context():
    buf, log = capture()
    tok = logs.request_id.set("req-12345678")
    try:
        log.info("hello %s", "world", extra={"route": "/x", "ms": 1.5})
    finally:
        logs.request_id.reset(tok)
    (doc,) = lines(buf)
    assert doc["msg"] == "hello world"
    assert doc["request_id"] == "req-12345678"
    assert doc["route"] == "/x" and doc["ms"] == 1.5
    assert doc["service"] == "svc" and doc["env"] == "test" and doc["release"] == "r1"
    assert doc["ts"].endswith("Z")


def test_sensitive_keys_redacted_recursively():
    buf, log = capture()
    log.info(
        "login",
        extra={
            "password": "hunter2hunter2",
            "headers": {"Authorization": "Bearer abc"},
            "user": {"api_key": "k", "name": "ok"},
        },
    )
    (doc,) = lines(buf)
    assert doc["password"] == "[redacted]"
    assert doc["headers"]["Authorization"] == "[redacted]"
    assert doc["user"] == {"api_key": "[redacted]", "name": "ok"}


def test_message_scrubbing():
    # 4111 1111 1111 1111 is the standard Visa test number and passes Luhn.
    text = logs.scrub_text(
        "card 4111 1111 1111 1111 for dana@example.com, Bearer abc.def, order 1234567890123"
    )
    assert "[card]" in text
    assert "d***@example.com" in text
    assert "Bearer [redacted]" in text
    assert "1234567890123" in text  # fails Luhn, left alone


def test_exceptions_are_logged():
    buf, log = capture()
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    (doc,) = lines(buf)
    assert "ValueError: boom" in doc["exc"]


@pytest.mark.parametrize(
    "key",
    ["password", "apiKey", "api_key", "X-Auth-Token", "card_number", "accountNumber", "set-cookie"],
)
def test_sensitive_key_words(key):
    assert logs.sensitive_key(key)


@pytest.mark.parametrize("key", ["discard_count", "bypass_cache", "compass", "user", "passenger"])
def test_substrings_are_not_sensitive(key):
    # Regression: substring matching redacted "discard_count" (card) and
    # "bypass_cache" (pass), hiding useful fields from the logs.
    assert not logs.sensitive_key(key)
