import pytest

from livesvc.auth import ROLES, AuthError, authenticate, main, mint

KEY = "k" * 40
NOW = 1_800_000_000.0


def test_round_trip():
    tok = mint(KEY, "u1", "user", ttl_s=60, now=NOW)
    who = authenticate(f"Bearer {tok}", KEY, now=NOW + 30)
    assert who.subject == "u1" and who.role == "user" and who.actor == "user:u1"
    assert who.policy is ROLES["user"]


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic abc", "Bearer ", "Bearer a.b.c", "Bearer " + "a" * 3000 + ".b"],
)
def test_malformed(header):
    with pytest.raises(AuthError):
        authenticate(header, KEY, now=NOW)


def test_expired_and_wrong_key():
    tok = mint(KEY, "u1", "user", ttl_s=60, now=NOW)
    with pytest.raises(AuthError, match="expired"):
        authenticate(f"Bearer {tok}", KEY, now=NOW + 61)
    with pytest.raises(AuthError, match="signature"):
        authenticate(f"Bearer {tok}", "z" * 40, now=NOW)


def test_tampered_role_rejected():
    import base64
    import json

    tok = mint(KEY, "u1", "user", ttl_s=60, now=NOW)
    payload, sig = tok.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["role"] = "support"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        authenticate(f"Bearer {forged}.{sig}", KEY, now=NOW)


def test_no_key_means_no_access():
    tok = mint(KEY, "u1", "user", now=NOW)
    with pytest.raises(AuthError, match="not configured"):
        authenticate(f"Bearer {tok}", None, now=NOW)


def test_mint_validates():
    with pytest.raises(ValueError):
        mint("short", "u1", "user")
    with pytest.raises(ValueError):
        mint(KEY, "u1", "root")


def test_cli(monkeypatch, capsys):
    monkeypatch.setenv("AUTH_SIGNING_KEY", KEY)
    assert main(["u1", "user"]) == 0
    tok = capsys.readouterr().out.strip()
    assert authenticate(f"Bearer {tok}", KEY).subject == "u1"
    assert main(["u1"]) == 2
    monkeypatch.setenv("AUTH_SIGNING_KEY", "short")
    assert main(["u1", "user"]) == 2
