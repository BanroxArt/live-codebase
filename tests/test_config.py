import pytest

from livesvc.config import ConfigError, load


def test_defaults():
    s = load({})
    assert s.env == "dev" and s.port == 8080 and not s.is_prod
    assert s.kill_switches == frozenset()


def test_parses_env():
    s = load(
        {
            "APP_ENV": "staging",
            "PORT": "9000",
            "KILL_SWITCHES": "a, b,,",
            "GIT_SHA": "abc",
            "TRACE_SAMPLE_RATE": "0.1",
        }
    )
    assert s.env == "staging" and s.port == 9000 and s.release == "abc"
    assert s.kill_switches == {"a", "b"}
    assert s.trace_sample_rate == 0.1


@pytest.mark.parametrize(
    "env",
    [
        {"APP_ENV": "production"},
        {"PORT": "http"},
        {"TRACE_SAMPLE_RATE": "2"},
        {"APP_ENV": "prod", "ADMIN_TOKEN": "short"},
    ],
)
def test_rejects(env):
    with pytest.raises(ConfigError):
        load(env)


def test_admin_token_not_in_repr():
    s = load({"ADMIN_TOKEN": "x" * 40})
    assert "x" * 40 not in repr(s)


@pytest.mark.parametrize(
    "env",
    [
        {"APP_ENV": "prod", "ADMIN_TOKEN": "a" * 40},
        {"AUTH_SIGNING_KEY": "short"},
        {"ADMIN_PORT": "8080"},
        {"PORT": "8080.5"},
        {"TRACE_TRUST_PARENT": "maybe"},
    ],
)
def test_rejects_auth_and_listener_config(env):
    with pytest.raises(ConfigError):
        load(env)


def test_prod_with_signing_key_loads():
    s = load({"APP_ENV": "prod", "AUTH_SIGNING_KEY": "s" * 40})
    assert s.is_prod and s.admin_host == "127.0.0.1" and s.admin_port == 9090
    assert "s" * 40 not in repr(s)
    assert s.trace_trust_parent is False
