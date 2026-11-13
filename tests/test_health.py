import time

from livesvc.health import Health


def test_ready_when_checks_pass():
    h = Health()
    h.register("ok", lambda: None)
    ok, results = h.ready()
    assert ok and results[0].ok


def test_failing_check_reports_class_name_only():
    h = Health()

    def bad():
        raise ConnectionError("db.internal.example:5432 refused")

    h.register("db", bad)
    ok, results = h.ready()
    assert not ok
    assert results[0].detail == "ConnectionError"


def test_slow_check_times_out():
    h = Health(timeout_s=0.05)
    h.register("slow", lambda: time.sleep(0.5))
    t0 = time.perf_counter()
    ok, results = h.ready()
    assert not ok and results[0].detail == "timeout"
    assert time.perf_counter() - t0 < 0.4


def test_draining_fails_readiness_without_running_checks():
    h = Health()
    calls = []
    h.register("x", lambda: calls.append(1))
    h.drain()
    ok, results = h.ready()
    assert not ok and results[0].name == "draining"
    assert calls == []
