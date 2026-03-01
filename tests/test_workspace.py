"""Tests for castor/workspace.py (issue #134)."""

import time

import pytest

from castor.workspace import WorkspaceManager

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr(tmp_path):
    return WorkspaceManager(store_dir=tmp_path / "workspaces")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_returns_metadata(mgr):
    ws = mgr.create("alpha", admin_email="alice@example.com")
    assert ws["name"] == "alpha"
    assert ws["admin_email"] == "alice@example.com"
    assert "id" in ws
    assert "token" in ws  # raw token shown once
    assert "token_hash" not in ws


def test_create_duplicate_raises(mgr):
    mgr.create("beta")
    with pytest.raises(ValueError, match="already exists"):
        mgr.create("beta")


def test_create_persists_to_disk(mgr):
    mgr.create("gamma")
    # Load a fresh manager from same dir
    mgr2 = WorkspaceManager(store_dir=mgr._dir)
    names = [w["name"] for w in mgr2.list()]
    assert "gamma" in names


def test_create_token_is_unique(mgr):
    ws1 = mgr.create("ws1")
    ws2 = mgr.create("ws2")
    assert ws1["token"] != ws2["token"]


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_existing(mgr):
    ws = mgr.create("delta")
    fetched = mgr.get(ws["id"])
    assert fetched is not None
    assert fetched["name"] == "delta"
    assert "token_hash" not in fetched


def test_get_missing_returns_none(mgr):
    assert mgr.get("nonexistent-id") is None


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty(mgr):
    assert mgr.list() == []


def test_list_sorted_by_creation(mgr):
    mgr.create("first")
    time.sleep(0.01)
    mgr.create("second")
    names = [w["name"] for w in mgr.list()]
    assert names == ["first", "second"]


def test_list_excludes_token_hash(mgr):
    mgr.create("check")
    for ws in mgr.list():
        assert "token_hash" not in ws


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_existing(mgr):
    ws = mgr.create("remove-me")
    result = mgr.delete(ws["id"])
    assert result is True
    assert mgr.get(ws["id"]) is None


def test_delete_nonexistent_returns_false(mgr):
    assert mgr.delete("ghost-id") is False


def test_delete_persists(mgr):
    ws = mgr.create("del-persist")
    mgr.delete(ws["id"])
    mgr2 = WorkspaceManager(store_dir=mgr._dir)
    assert mgr2.get(ws["id"]) is None


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


def test_verify_token_correct(mgr):
    ws = mgr.create("verify-ok")
    assert mgr.verify_token(ws["id"], ws["token"]) is True


def test_verify_token_wrong_token(mgr):
    ws = mgr.create("verify-bad")
    assert mgr.verify_token(ws["id"], "wrong-token") is False


def test_verify_token_unknown_workspace(mgr):
    assert mgr.verify_token("no-such-id", "anything") is False


# ---------------------------------------------------------------------------
# issue_token
# ---------------------------------------------------------------------------


def test_issue_token_returns_string(mgr):
    ws = mgr.create("token-ws")
    token = mgr.issue_token(ws["id"], role="operator")
    assert isinstance(token, str)
    assert len(token) > 0


def test_issue_token_unknown_raises(mgr):
    with pytest.raises(ValueError, match="not found"):
        mgr.issue_token("ghost-id", role="viewer")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_returns_dict(mgr):
    ws = mgr.create("status-ws")
    s = mgr.status(ws["id"])
    assert s["id"] == ws["id"]
    assert s["name"] == "status-ws"
    assert isinstance(s["enabled"], bool)
    assert "rcan_configured" in s
    assert "memory_initialized" in s
    assert "uptime_s" in s


def test_status_unknown_raises(mgr):
    with pytest.raises(ValueError, match="not found"):
        mgr.status("no-such-id")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_manager_singleton(tmp_path):
    import castor.workspace as ws_mod

    original = ws_mod._manager
    ws_mod._manager = None
    try:
        m1 = ws_mod.get_manager()
        m2 = ws_mod.get_manager()
        assert m1 is m2
    finally:
        ws_mod._manager = original
