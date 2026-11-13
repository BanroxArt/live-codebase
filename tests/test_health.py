import threading
import time

import pytest

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


def test_hung_check_does_not_pile_up_or_starve_others():
    # Regression: a check that never returned was resubmitted on every probe.
    # After four probes the pool was full of stuck copies and every check,
    # healthy ones included, timed out from then on.
    release = threading.Event()
    starts = []

    def hung():
        starts.append(1)
        release.wait(5)

    h = Health(timeout_s=0.05, max_workers=4)
    h.register("hung", hung)
    h.register("fine", lambda: None)
    try:
        ok, results = h.ready()
        assert not ok and {r.name: r.detail for r in results}["hung"] == "timeout"
        for _ in range(10):
            ok, results = h.ready()
            by_name = {r.name: r for r in results}
            assert by_name["hung"].detail == "still running"
            assert by_name["fine"].ok
        assert len(starts) == 1
        release.set()
        time.sleep(0.05)
        ok, _ = h.ready()
        assert ok and len(starts) == 2
    finally:
        release.set()
        h.close()


def test_checks_run_in_parallel():
    h = Health(timeout_s=1.0)
    for i in range(4):
        h.register(f"c{i}", lambda: time.sleep(0.1))
    t0 = time.perf_counter()
    ok, _ = h.ready()
    assert ok and time.perf_counter() - t0 < 0.3
    h.close()


def test_too_many_checks_rejected():
    h = Health(max_workers=2)
    h.register("a", lambda: None)
    h.register("b", lambda: None)
    h.register("a", lambda: None)  # replacing is fine
    with pytest.raises(ValueError):
        h.register("c", lambda: None)
    h.close()
