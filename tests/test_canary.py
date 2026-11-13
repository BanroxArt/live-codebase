from livesvc.canary import Stats, Thresholds, decide, main, stats_from_metrics
from livesvc.metrics import Registry


def scrape(ok: int, errors: int, latency: float) -> str:
    r = Registry()
    r.counter("http_requests_total", "x")
    r.histogram("http_request_duration_seconds", "x")
    for _ in range(ok):
        r.inc("http_requests_total", route="/api", method="GET", status="200")
        r.observe("http_request_duration_seconds", latency, route="/api")
    for _ in range(errors):
        r.inc("http_requests_total", route="/api", method="GET", status="500")
        r.observe("http_request_duration_seconds", latency, route="/api")
    r.inc("http_requests_total", 1000, route="/healthz", method="GET", status="200")
    return r.render()


def test_stats_from_metrics_skips_probes():
    s = stats_from_metrics(scrape(90, 10, 0.03))
    assert s.requests == 100 and s.errors == 10
    assert 0.025 <= s.p95_s <= 0.05


def test_promote_hold_rollback():
    base = Stats(1000, 5, 0.1)
    assert decide(base, Stats(50, 0, 0.1)).action == "hold"
    assert decide(base, Stats(1000, 6, 0.11)).action == "promote"
    v = decide(base, Stats(1000, 40, 0.1))
    assert v.action == "rollback" and "error rate" in v.reasons[0]
    v = decide(base, Stats(1000, 5, 0.3))
    assert v.action == "rollback" and "p95" in v.reasons[0]


def test_small_absolute_change_on_zero_baseline_is_ok():
    assert decide(Stats(1000, 0, 0.1), Stats(1000, 3, 0.1), Thresholds()).action == "promote"
    assert decide(Stats(1000, 0, 0.1), Stats(1000, 30, 0.1), Thresholds()).action == "rollback"


def test_cli_exit_codes(tmp_path, capsys):
    b, c = tmp_path / "b.txt", tmp_path / "c.txt"
    b.write_text(scrape(990, 10, 0.03))
    c.write_text(scrape(900, 100, 0.03))
    assert main([str(b), str(c)]) == 1
    assert '"rollback"' in capsys.readouterr().out
    c.write_text(scrape(995, 5, 0.03))
    assert main([str(b), str(c)]) == 0
    assert main([str(b), str(c), "--min-requests", "5000"]) == 3


def test_admin_listener_traffic_is_ignored():
    text = "\n".join(
        [
            'http_requests_total{listener="public",method="GET",route="/api/x",status="200"} 100',
            'http_requests_total{listener="admin",method="GET",route="/version",status="500"} 50',
        ]
    )
    s = stats_from_metrics(text)
    assert s.requests == 100 and s.errors == 0
