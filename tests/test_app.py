import threading

from conftest import ADMIN, SIGNING, Client, bearer

from livesvc.app import App
from livesvc.auth import mint
from livesvc.config import Settings
from livesvc.data import Store
from livesvc.flags import Flags

TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_healthz_and_version(client, admin_client):
    status, _, body = client.get("/healthz")
    assert status == 200 and body["status"] == "ok"
    assert admin_client.get("/version")[2] == {
        "service": "livesvc",
        "env": "test",
        "release": "abc123",
    }


def test_admin_surface_not_on_public_listener(client, admin_client):
    for path in ("/metrics", "/version", "/admin/flags"):
        assert client.get(path)[0] == 404, path
    assert (
        client.request("POST", "/admin/flags/new_recs/kill", {"authorization": f"Bearer {ADMIN}"})[
            0
        ]
        == 404
    )
    assert admin_client.get("/metrics")[0] == 200
    assert admin_client.get("/api/recommendations")[0] == 404
    assert admin_client.get("/readyz")[0] == 200


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


def test_404_405_and_metrics_use_route_templates(client, admin_client):
    assert client.get("/nope")[0] == 404
    assert client.post("/healthz")[0] == 405
    client.post("/api/users/u1/profile")
    client.post("/api/users/u2/profile")
    client.get("/api/users/u1/profile", **bearer("u1", "user"))
    client.request("BREW", "/healthz")
    _, _, text = admin_client.get("/metrics")
    assert 'route="/api/users/{user_id}/profile"' in text
    assert "/api/users/u1" not in text and "/api/users/u2" not in text
    assert (
        'http_requests_total{listener="public",method="GET",route="unmatched",status="404"} 1'
        in text
    )
    # 405s are labelled with the template, so arbitrary ids cannot add series.
    assert (
        'http_requests_total{listener="public",method="POST",'
        'route="/api/users/{user_id}/profile",status="405"} 2' in text
    )
    assert 'method="other",route="/healthz",status="405"' in text
    assert "BREW" not in text


def test_profile_requires_a_token(client):
    status, h, _body = client.get("/api/users/u1/profile")
    assert status == 401 and h["www-authenticate"].startswith("Bearer")
    assert client.get("/api/users/u1/profile", authorization="Bearer junk.sig")[0] == 401
    forged = mint("f" * 40, "u1", "support")
    assert client.get("/api/users/u1/profile", authorization=f"Bearer {forged}")[0] == 401
    expired = mint(SIGNING, "u1", "user", ttl_s=-1)
    assert client.get("/api/users/u1/profile", authorization=f"Bearer {expired}")[0] == 401


def test_profile_disabled_without_signing_key(store, flags):
    c = Client(App(Settings(env="test"), store, flags))
    tok = mint(SIGNING, "u1", "user")
    status, _, body = c.get("/api/users/u1/profile", authorization=f"Bearer {tok}")
    assert status == 401 and "not configured" in body["error"]


def test_user_reads_only_own_profile(client, store):
    status, _, body = client.get("/api/users/u1/profile", **bearer("u1", "user"))
    assert status == 200
    assert body["data"] == {
        "id": "u1",
        "display_name": "Dana",
        "email": "dana@example.com",
        "plan": "pro",
    }
    status, _, body = client.get("/api/users/u2/profile", **bearer("u1", "user"))
    assert status == 403 and body["error"] == "denied_not_owner"
    assert store.access_log("u2")[-1] == ("user:u1", "service", "", "denied_not_owner")


def test_purpose_and_actor_come_from_the_token(client, store):
    # Regression: purpose and actor used to be read from the query string and
    # an X-Actor header, so any caller could pick "support" and any name.
    tok = bearer("u1", "user")
    status, _, _ = client.get("/api/users/u1/profile?purpose=support", **tok)
    assert status == 400
    status, _, _ = client.get("/api/users/u1/profile", x_actor="admin", **tok)
    assert status == 400
    client.get("/api/users/u1/profile", **tok)
    assert store.access_log("u1")[-1][:2] == ("user:u1", "service")


def test_support_role_gets_masked_email(client, store):
    status, _, body = client.get("/api/users/u1/profile", **bearer("agent-7", "support"))
    assert status == 200
    assert body["data"]["email"] != "dana@example.com" and "@example.com" in body["data"]["email"]
    assert store.access_log("u1")[-1][:2] == ("support:agent-7", "support")


def test_analytics_role_needs_consent_and_no_email(client):
    tok = bearer("job-1", "analytics")
    assert client.get("/api/users/u1/profile", **tok)[0] == 403  # no consent
    status, _, body = client.get("/api/users/u2/profile", **tok)
    assert status == 200 and "email" not in body["data"]
    status, _, body = client.get("/api/users/u2/profile?fields=id,email", **tok)
    assert status == 403 and body == {"error": "denied_role_fields", "fields": ["email"]}


def test_fields_can_narrow_but_not_widen(client):
    tok = bearer("u1", "user")
    status, _, body = client.get("/api/users/u1/profile?fields=id,plan", **tok)
    assert status == 200 and body["data"] == {"id": "u1", "plan": "pro"}
    assert client.get("/api/users/u1/profile?fields=created_at", **tok)[0] == 403
    assert client.get("/api/users/zz/profile", **bearer("zz", "user"))[0] == 404


def test_flagged_endpoint_is_sticky_per_user(client):
    seen = {client.get("/api/recommendations", x_user_id="user-42")[2]["variant"] for _ in range(5)}
    assert len(seen) == 1
    _, h, body = client.get("/api/recommendations", x_user_id="qa")
    assert body["variant"] == "new" and h["x-flag-reason"] == "allow_list"


def test_kill_switch_via_admin(client, admin_client):
    auth = {"authorization": f"Bearer {ADMIN}"}
    assert admin_client.post("/admin/flags/new_recs/kill")[0] == 401
    assert (
        admin_client.request(
            "POST", "/admin/flags/new_recs/kill", {"authorization": "Bearer nope"}
        )[0]
        == 401
    )
    status, _, body = admin_client.request("POST", "/admin/flags/new_recs/kill", auth)
    assert status == 202 and body["killed"] == ["new_recs"]
    _, h, body = client.get("/api/recommendations", x_user_id="qa")
    assert body["variant"] == "control" and h["x-flag-reason"] == "killed"
    assert admin_client.request("GET", "/admin/flags", auth)[2]["killed"] == ["new_recs"]
    admin_client.request("POST", "/admin/flags/new_recs/revive", auth)
    assert client.get("/api/recommendations", x_user_id="qa")[2]["variant"] == "new"


def test_admin_disabled_without_token(store, flags):
    c = Client(App(Settings(env="test"), store, flags), admin=True)
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


def test_concurrent_profile_reads_share_one_connection(tmp_path, flags):
    # Regression: only Store.read held the lock, so readiness pings and
    # consent writes from other threads could hit the shared connection
    # mid-statement.
    store = Store(str(tmp_path / "db.sqlite"), "test")
    store.add_user("u1", "Dana", "dana@example.com")
    app = App(Settings(env="test", auth_signing_key=SIGNING), store, flags)
    c = Client(app)
    tok = bearer("u1", "user")
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(50):
                assert c.get("/api/users/u1/profile", **tok)[0] == 200
                assert c.get("/readyz")[0] == 200
        except BaseException as exc:
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(50):
                store.grant("u1", "analytics", f"v{i}", at=float(i))
                store.revoke("u1", "analytics")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)] + [
        threading.Thread(target=writer)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    app.health.close()
    store.close()
    assert errors == []
