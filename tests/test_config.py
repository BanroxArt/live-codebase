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
