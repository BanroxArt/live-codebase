from livesvc.metrics import Registry


def test_counter_and_histogram_render():
    r = Registry()
    r.counter("reqs", "Requests.")
    r.histogram("lat", "Latency.", buckets=(0.1, 1.0))
    r.inc("reqs", route="/a", status="200")
    r.inc("reqs", route="/a", status="200")
    r.observe("lat", 0.05, route="/a")
    r.observe("lat", 0.5, route="/a")
    r.observe("lat", 3.0, route="/a")
    text = r.render()
    assert "# TYPE reqs counter" in text
    assert 'reqs{route="/a",status="200"} 2' in text
    assert 'lat_bucket{le="0.1",route="/a"} 1' in text
    assert 'lat_bucket{le="1",route="/a"} 2' in text
    assert 'lat_bucket{le="+Inf",route="/a"} 3' in text
    assert 'lat_count{route="/a"} 3' in text
    assert r.value("reqs", route="/a", status="200") == 2


def test_label_values_are_escaped():
    r = Registry()
    r.counter("c", "x")
    r.inc("c", path='a"b\\c')
    assert 'c{path="a\\"b\\\\c"} 1' in r.render()
