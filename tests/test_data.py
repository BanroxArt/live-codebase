import pytest

from livesvc.data import EnvironmentMismatch, PurposeError, Store, mask_email


def test_service_read_is_allowed_and_audited(store):
    r = store.read("u1", "service", ["id", "plan"], actor="api", request_id="r1")
    assert r.data == {"id": "u1", "plan": "pro"}
    assert r.decision == "allowed"
    assert store.access_log("u1") == [("api", "service", "id,plan", "allowed")]


def test_consent_required_purposes(store):
    denied = store.read("u1", "analytics", ["id", "plan"], actor="etl")
    assert denied.decision == "denied_no_consent" and denied.data == {}
    ok = store.read("u2", "analytics", ["id", "plan"], actor="etl")
    assert ok.decision == "allowed"


def test_revocation_takes_effect_immediately(store):
    assert store.revoke("u2", "analytics") == 1
    assert store.read("u2", "analytics", ["id"], actor="etl").decision == "denied_no_consent"


def test_fields_outside_purpose_are_withheld(store):
    store.grant("u1", "analytics", "v3")
    r = store.read("u1", "analytics", ["id", "email"], actor="etl")
    assert r.decision == "partial"
    assert r.data == {"id": "u1"}
    assert r.withheld == ("email",)
    assert store.read("u1", "analytics", ["email"], actor="etl").decision == "denied_purpose"


def test_support_sees_masked_email(store):
    r = store.read("u1", "support", ["email"], actor="agent-7")
    assert r.data == {"email": "d***@example.com"}


def test_unknown_purpose_or_field_raises(store):
    with pytest.raises(PurposeError):
        store.read("u1", "resale", ["id"], actor="x")
    with pytest.raises(PurposeError):
        store.read("u1", "service", ["id; DROP TABLE users"], actor="x")


def test_missing_user(store):
    assert store.read("nope", "service", ["id"], actor="x") is None
    assert store.access_log("nope")[0][3] == "not_found"


def test_database_stamped_with_environment(tmp_path):
    path = str(tmp_path / "app.db")
    Store(path, "prod").close()
    with pytest.raises(EnvironmentMismatch):
        Store(path, "dev")
    Store(path, "prod").close()


def test_mask_email():
    assert mask_email("dana@example.com") == "d***@example.com"
    assert mask_email("nonsense") == "***"
