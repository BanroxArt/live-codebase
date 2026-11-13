import json
import os
from datetime import date

import pytest

from livesvc.flags import BUCKETS, FlagError, Flags, bucket, parse_flags


def make(rollout, **extra):
    return Flags("prod", parse_flags({"f": {"owner": "me", "rollout": {"prod": rollout}, **extra}}))


def test_bucket_is_stable_and_in_range():
    assert bucket("f", "user-1") == bucket("f", "user-1")
    assert all(0 <= bucket("f", f"u{i}") < BUCKETS for i in range(1000))


def test_bucket_differs_per_flag():
    a = [bucket("a", f"u{i}") for i in range(200)]
    b = [bucket("b", f"u{i}") for i in range(200)]
    assert a != b


def test_rollout_share_is_close_to_target():
    f = make(20)
    on = sum(f.enabled("f", f"user-{i}") for i in range(20_000))
    assert 0.18 < on / 20_000 < 0.22


def test_raising_rollout_never_turns_anyone_off():
    users = [f"user-{i}" for i in range(5_000)]
    on_5 = {u for u in users if make(5).enabled("f", u)}
    on_20 = {u for u in users if make(20).enabled("f", u)}
    assert on_5 <= on_20
    assert len(on_20) > len(on_5)


def test_zero_and_hundred():
    assert not any(make(0).enabled("f", f"u{i}") for i in range(500))
    assert all(make(100).enabled("f", f"u{i}") for i in range(500))


def test_order_kill_deny_allow():
    f = make(100, deny=["bad"], allow=["vip"])
    assert f.evaluate("f", "bad").reason == "deny_list"
    assert make(0, allow=["vip"]).evaluate("f", "vip").reason == "allow_list"
    f.kill("f")
    assert f.evaluate("f", "vip").reason == "killed"
    f.revive("f")
    assert f.enabled("f", "anyone")


def test_global_kill_and_env_kills():
    f = Flags(
        "prod", parse_flags({"f": {"owner": "me", "rollout": {"prod": 100}}}), kill_switches={"*"}
    )
    assert f.evaluate("f", "u").reason == "killed"


def test_unknown_flag_is_off_and_missing_env_uses_default():
    f = Flags("dev", parse_flags({"f": {"owner": "me", "default": True, "rollout": {"prod": 0}}}))
    assert f.evaluate("nope", "u").reason == "unknown_flag"
    ev = f.evaluate("f", "u")
    assert (ev.enabled, ev.reason) == (True, "default")


def test_no_unit_id_only_on_at_full_rollout():
    assert make(50).evaluate("f", None).reason == "no_unit_id"
    assert not make(50).enabled("f", None)
    assert make(100).enabled("f", None)


@pytest.mark.parametrize(
    "raw",
    [
        {"f": {"rollout": {"prod": 5}}},
        {"f": {"owner": "x", "rollout": {"prod": 150}}},
        {"f": {"owner": "x", "rollout": {"prod": "lots"}}},
        {"f": {"owner": "x", "remove_by": "next week"}},
        {"f": "on"},
        [],
    ],
)
def test_parse_rejects(raw):
    with pytest.raises(FlagError):
        parse_flags(raw)


def test_stale_flags():
    f = Flags(
        "prod",
        parse_flags(
            {
                "old": {"owner": "x", "remove_by": "2026-01-01"},
                "new": {"owner": "x", "remove_by": "2027-01-01"},
            }
        ),
    )
    assert [x.name for x in f.stale(date(2026, 6, 1))] == ["old"]


def test_file_reload_and_kill_from_file(tmp_path):
    p = tmp_path / "flags.json"
    p.write_text(json.dumps({"f": {"owner": "x", "rollout": {"prod": 100}}}))
    f = Flags("prod", path=p, check_every_s=0)
    assert f.loaded and f.enabled("f", "u")
    p.write_text(json.dumps({"f": {"owner": "x", "rollout": {"prod": 100}, "killed": True}}))
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert f.evaluate("f", "u").reason == "killed"
    assert "f" in f.snapshot()["killed"]


def test_bad_file_keeps_last_good(tmp_path):
    p = tmp_path / "flags.json"
    p.write_text(json.dumps({"f": {"owner": "x", "rollout": {"prod": 100}}}))
    f = Flags("prod", path=p, check_every_s=0)
    p.write_text("{not json")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert f.reload() is False
    assert f.enabled("f", "u")


def test_missing_file_means_not_loaded(tmp_path):
    assert not Flags("prod", path=tmp_path / "missing.json").loaded
