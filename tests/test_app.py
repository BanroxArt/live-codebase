from conftest import ADMIN, Client

from livesvc.app import App
from livesvc.config import Settings
from livesvc.flags import Flags

TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_healthz_and_version(client):
    status, _, body = client.get("/healthz")
    assert status == 200 and body["status"] == "ok"
    assert client.get("/version")[2] == {"service": "livesvc", "env": "test", "release": "abc123"}


def test_readyz_flips_when_draining(client, app):
    assert client.get("/readyz")[0] == 200
    app.health.drain()
    status, _, body = client.get("/readyz")
    assert status == 503 and body["checks"][0]["name"] == "draining"


def test_request_id_propagated_or_generated(client):
    _, h, _ = client.get("/healthz", x_request_id="abc-12345678")
    assert h["x-request-id"] == "abc-12345678"
    _, h, _ = client.get("/healthz", x_request_id="bad id with spaces")
    assert h["x-request-id"] != "bad id with spaces" and len(h["x-request-id"]) == 32


def test_trace_continues_from_caller(client):
    _, h, _ = client.get("/healthz", traceparent=TP)
    assert h["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
    assert not h["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7")


def test_404_405_and_metrics_use_route_templates(client):
    assert client.get("/nope")[0] == 404
    assert client.post("/healthz")[0] == 405
    client.get("/api/users/u1/profile")
    client.get("/api/users/u2/profile")
    _, _, text = client.get("/metrics")
    assert 'route="/api/users/{user_id}/profile"' in text
    assert "/api/users/u1" not in text
    assert 'http_requests_total{method="GET",route="unmatched",status="404"} 1' in text


def test_profile_consent_paths(client):
    status, _, body = client.get("/api/users/u1/profile?fields=id,plan")
    assert status == 200 and body["data"] == {"id": "u1", "plan": "pro"}
    assert client.get("/api/users/u1/profile?purpose=marketing&fields=email")[0] == 403
    assert client.get("/api/users/u1/profile?purpose=resale")[0] == 400
    assert client.get("/api/users/zz/profile")[0] == 404


def test_flagged_endpoint_is_sticky_per_user(client):
    seen = {client.get("/api/recommendations", x_user_id="user-42")[2]["variant"] for _ in range(5)}
    assert len(seen) == 1
    _, h, body = client.get("/api/recommendations", x_user_id="qa")
    assert body["variant"] == "new" and h["x-flag-reason"] == "allow_list"


def test_kill_switch_via_admin(client):
    auth = {"authorization": f"Bearer {ADMIN}"}
    assert client.post("/admin/flags/new_recs/kill")[0] == 401
    assert (
        client.request("POST", "/admin/flags/new_recs/kill", {"authorization": "Bearer nope"})[0]
        == 401
    )
    status, _, body = client.request("POST", "/admin/flags/new_recs/kill", auth)
    assert status == 202 and body["killed"] == ["new_recs"]
    _, h, body = client.get("/api/recommendations", x_user_id="qa")
    assert body["variant"] == "control" and h["x-flag-reason"] == "killed"
    assert client.request("GET", "/admin/flags", auth)[2]["killed"] == ["new_recs"]
    client.request("POST", "/admin/flags/new_recs/revive", auth)
    assert client.get("/api/recommendations", x_user_id="qa")[2]["variant"] == "new"


def test_admin_disabled_without_token(store, flags):
    c = Client(App(Settings(env="test"), store, flags))
    assert c.request("GET", "/admin/flags", {"authorization": "Bearer "})[0] == 401


def test_unhandled_error_is_500_without_details(app, client):
    def boom(req):
        raise RuntimeError("secret internals")

    app.route("GET", "/boom", boom)
    status, _, body = client.get("/boom")
    assert status == 500
    assert body["error"] == "internal error" and "secret" not in str(body)
    assert "request_id" in body


def test_readyz_fails_if_flags_file_missing(store, tmp_path):
    flags = Flags("test", path=tmp_path / "missing.json")
    c = Client(App(Settings(env="test"), store, flags))
    status, _, body = c.get("/readyz")
    assert status == 503
    assert {x["name"]: x["ok"] for x in body["checks"]}["flags"] is False
