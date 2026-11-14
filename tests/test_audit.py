"""Hash-chained access log: tamper detection, retention, key handling."""

import hashlib
import sqlite3
import threading

import pytest
from conftest import ADMIN, Client
from hypothesis import given, settings
from hypothesis import strategies as st

from livesvc.app import App
from livesvc.audit import GENESIS, AuditChain, ChainConfigError, Head, canonical, main
from livesvc.config import ConfigError, Settings, load
from livesvc.data import Store

KEY = b"0123456789abcdef" * 2  # test-only chain key, 32 bytes


def keyed_store(path=":memory:", key=KEY):
    s = Store(path, "test", key)
    s.add_user("u1", "Dana", "dana@example.com", plan="pro")
    return s


def reads(store, n):
    for i in range(n):
        store.read("u1", "service", ["id", "plan"], actor=f"api-{i}", request_id=f"r{i}")


def raw_edit(db, sql, params=()):
    """What someone with shell access does: drop the triggers, then write."""
    db.execute("DROP TRIGGER access_log_no_update")
    db.execute("DROP TRIGGER access_log_no_delete")
    db.execute(sql, params)


def test_every_read_extends_the_chain():
    s = keyed_store()
    assert s.audit_head() == Head(0, GENESIS)
    reads(s, 5)
    v = s.verify_audit()
    assert v.ok and v.checked == 5 and v.head.seq == 5
    assert v.head == s.audit_head()
    rows = s.db.execute("SELECT seq, prev_hash, row_hash FROM access_log ORDER BY seq").fetchall()
    assert rows[0][1] == GENESIS
    assert all(rows[i][1] == rows[i - 1][2] for i in range(1, len(rows)))


def test_update_and_delete_are_refused_by_triggers():
    s = keyed_store()
    reads(s, 2)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.db.execute("UPDATE access_log SET decision = 'allowed' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.db.execute("DELETE FROM access_log WHERE seq = 2")
    assert s.verify_audit().ok


def test_edited_row_is_found():
    s = keyed_store()
    reads(s, 6)
    raw_edit(s.db, "UPDATE access_log SET actor = 'nobody' WHERE seq = 4")
    v = s.verify_audit()
    assert not v.ok and v.first_bad_seq == 4
    assert "row contents" in v.reason
    assert v.checked == 3


def test_deleted_middle_row_is_a_gap():
    s = keyed_store()
    reads(s, 6)
    raw_edit(s.db, "DELETE FROM access_log WHERE seq = 3")
    v = s.verify_audit()
    assert not v.ok and v.first_bad_seq == 3 and "gap" in v.reason


def test_recomputing_without_the_key_does_not_help():
    s = keyed_store()
    reads(s, 3)
    # Rewrite row 3 and recompute its hash the only way possible without the
    # key: plain SHA-256 over the canonical form.
    seq, prev, ts, user_id, purpose, fields, decision, rid = s.db.execute(
        "SELECT seq, prev_hash, ts, user_id, purpose, fields, decision, request_id"
        " FROM access_log WHERE seq = 3"
    ).fetchone()
    forged = canonical(seq, prev, ts, "nobody", user_id, purpose, fields, decision, rid)
    raw_edit(
        s.db,
        "UPDATE access_log SET actor = 'nobody', row_hash = ? WHERE seq = 3",
        (hashlib.sha256(forged).hexdigest(),),
    )
    v = s.verify_audit()
    assert not v.ok and v.first_bad_seq == 3


def test_unkeyed_chain_can_be_recomputed_which_is_why_the_key_exists():
    s = keyed_store(key=None)
    reads(s, 1)
    seq, prev, ts, user_id, purpose, fields, decision, rid = s.db.execute(
        "SELECT seq, prev_hash, ts, user_id, purpose, fields, decision, request_id"
        " FROM access_log WHERE seq = 1"
    ).fetchone()
    forged = canonical(seq, prev, ts, "nobody", user_id, purpose, fields, decision, rid)
    raw_edit(
        s.db,
        "UPDATE access_log SET actor = 'nobody', row_hash = ? WHERE seq = 1",
        (hashlib.sha256(forged).hexdigest(),),
    )
    assert s.verify_audit().ok
    assert not s.audit.keyed


def test_truncation_needs_an_outside_checkpoint():
    s = keyed_store()
    reads(s, 5)
    checkpoint = s.audit_head()
    raw_edit(s.db, "DELETE FROM access_log WHERE seq >= 4")
    assert s.verify_audit().ok  # a shorter chain is still a valid chain
    v = s.verify_audit(checkpoint)
    assert not v.ok and v.first_bad_seq == 5 and "ends at 3" in v.reason


def test_checkpoint_with_a_different_hash_is_rewritten_history():
    s = keyed_store()
    reads(s, 3)
    head = s.audit_head()
    assert s.verify_audit(head).ok
    v = s.verify_audit(Head(head.seq, "f" * 64))
    assert not v.ok and "different hash" in v.reason


def test_prune_keeps_the_rest_verifiable():
    s = keyed_store()
    reads(s, 6)
    cutoff = s.db.execute("SELECT ts FROM access_log WHERE seq = 4").fetchone()[0]
    old_head = s.audit_head()
    assert s.prune_audit(cutoff) == 3
    v = s.verify_audit(old_head)
    assert v.ok and v.anchor.seq == 3 and v.checked == 3
    reads(s, 1)
    assert s.audit_head().seq == 7
    assert s.verify_audit().ok
    # The delete trigger is back in force after the prune.
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("DELETE FROM access_log WHERE seq = 7")


def test_prune_everything_then_append_continues_the_sequence():
    s = keyed_store()
    reads(s, 3)
    assert s.prune_audit(float("inf")) == 3
    assert s.audit_head().seq == 3
    reads(s, 1)
    assert s.audit_head().seq == 4 and s.verify_audit().ok


def test_checkpoint_older_than_anchor_is_reported_not_failed():
    s = keyed_store()
    reads(s, 4)
    early = Head(1, s.db.execute("SELECT row_hash FROM access_log WHERE seq = 1").fetchone()[0])
    s.prune_audit(float("inf"))
    v = s.verify_audit(early)
    assert v.ok and "older than the prune anchor" in v.reason


def test_chain_mode_and_key_are_pinned(tmp_path):
    path = str(tmp_path / "db.sqlite")
    keyed_store(path).close()
    with pytest.raises(ChainConfigError, match="hmac-sha256"):
        Store(path, "test")
    with pytest.raises(ChainConfigError, match="key id"):
        Store(path, "test", b"another-key-of-32-bytes-or-more!")
    Store(path, "test", KEY).close()


def test_short_key_is_refused():
    with pytest.raises(ChainConfigError):
        Store(":memory:", "test", b"short")
    with pytest.raises(ConfigError):
        load({"AUDIT_CHAIN_KEY": "short"})
    s = load({"AUDIT_CHAIN_KEY": "x" * 32})
    assert "x" * 32 not in repr(s)


def test_old_database_is_migrated_and_legacy_rows_are_counted(tmp_path):
    path = str(tmp_path / "old.sqlite")
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('env', 'test');
        CREATE TABLE access_log (ts REAL NOT NULL, actor TEXT NOT NULL, user_id TEXT NOT NULL,
          purpose TEXT NOT NULL, fields TEXT NOT NULL, decision TEXT NOT NULL, request_id TEXT);
        INSERT INTO access_log VALUES (1.0, 'api', 'u1', 'service', 'id', 'allowed', NULL);
        INSERT INTO access_log VALUES (2.0, 'api', 'u1', 'service', 'id', 'allowed', NULL);
        """
    )
    db.commit()
    db.close()
    s = keyed_store(path)
    reads(s, 2)
    v = s.verify_audit()
    assert v.ok and v.unchained == 2 and v.checked == 2
    assert len(s.access_log("u1")) == 4
    s.close()


def test_two_connections_on_one_file_do_not_fork_the_chain(tmp_path):
    path = str(tmp_path / "shared.sqlite")
    a = keyed_store(path)
    b = Store(path, "test", KEY)

    def worker(store, tag):
        for i in range(25):
            store.read("u1", "service", ["id"], actor=f"{tag}-{i}")

    threads = [threading.Thread(target=worker, args=(st_, t)) for st_, t in ((a, "a"), (b, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    v = a.verify_audit()
    assert v.ok and v.checked == 50
    a.close()
    b.close()


def test_head_parse_rejects_garbage():
    for bad in ("", "12", "x:" + "0" * 64, "3:" + "z" * 64, "3:abc"):
        with pytest.raises(ValueError):
            Head.parse(bad)
    assert Head.parse(f"7:{'A' * 64}") == Head(7, "a" * 64)


def test_chain_repr_hides_key():
    s = keyed_store()
    assert "0123456789" not in repr(s.audit)


def test_cli_verify_and_head(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "db.sqlite")
    s = keyed_store(path)
    reads(s, 3)
    head = s.audit_head()
    s.close()
    monkeypatch.setenv("AUDIT_CHAIN_KEY", KEY.decode())
    assert main(["head", path]) == 0
    assert capsys.readouterr().out.strip() == str(head)
    assert main(["verify", path, "--expect", str(head)]) == 0
    assert "ok: 3 row(s)" in capsys.readouterr().out
    db = sqlite3.connect(path)
    raw_edit(db, "UPDATE access_log SET decision = 'allowed', purpose = 'support' WHERE seq = 2")
    db.commit()
    db.close()
    assert main(["verify", path]) == 1
    assert "first bad row 2" in capsys.readouterr().out
    monkeypatch.delenv("AUDIT_CHAIN_KEY")
    assert main(["verify", path]) == 2
    assert main(["verify", str(tmp_path / "missing.sqlite")]) == 2


def test_cli_does_not_write_to_the_database(tmp_path, monkeypatch):
    path = str(tmp_path / "db.sqlite")
    s = keyed_store(path)
    reads(s, 1)
    s.close()
    before = (tmp_path / "db.sqlite").read_bytes()
    monkeypatch.setenv("AUDIT_CHAIN_KEY", KEY.decode())
    main(["verify", path])
    assert (tmp_path / "db.sqlite").read_bytes() == before


def test_admin_audit_head(store, flags):
    app = App(Settings(env="test", admin_token=ADMIN), store, flags)
    c = Client(app, admin=True)
    assert c.get("/admin/audit/head")[0] == 401
    assert Client(app).get("/admin/audit/head")[0] == 404
    store.read("u1", "service", ["id"], actor="api")
    status, _, body = c.request("GET", "/admin/audit/head", {"authorization": f"Bearer {ADMIN}"})
    assert status == 200
    assert (body["seq"], body["hash"]) == (store.audit_head().seq, store.audit_head().hash)
    assert body["keyed"] is False
    app.health.close()


@settings(max_examples=40, deadline=None)
@given(
    st.integers(min_value=1, max_value=12),
    st.data(),
    st.sampled_from(["actor", "user_id", "purpose", "fields", "decision", "request_id", "ts"]),
)
def test_any_single_field_edit_is_pinned_to_its_row(n, data, column):
    s = keyed_store()
    reads(s, n)
    target = data.draw(st.integers(min_value=1, max_value=n))
    value = 12345.5 if column == "ts" else "tampered"
    raw_edit(s.db, f"UPDATE access_log SET {column} = ? WHERE seq = ?", (value, target))
    v = s.verify_audit()
    assert not v.ok and v.first_bad_seq == target
    s.close()


def test_chain_on_raw_connection_without_store():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE access_log (ts REAL NOT NULL, actor TEXT NOT NULL, user_id TEXT NOT NULL,"
        " purpose TEXT NOT NULL, fields TEXT NOT NULL, decision TEXT NOT NULL, request_id TEXT);"
    )
    chain = AuditChain(db, KEY)
    chain.append(1.0, "a", "u", "service", ["b", "a"], "allowed", None)
    assert db.execute("SELECT fields FROM access_log").fetchone()[0] == "a,b"
    assert chain.verify().ok
