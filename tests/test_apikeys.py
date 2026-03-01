"""
tests/test_apikeys.py — Unit + API tests for castor/apikeys.py.

Covers:
  - ApiKeyManager: generate, verify, revoke, list, purge_expired
  - Role validation
  - Expiry logic
  - API: POST /api/keys/generate, GET /api/keys/list, DELETE /api/keys/{id}
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_mgr(tmp_path):
    from castor.apikeys import ApiKeyManager

    return ApiKeyManager(store_path=tmp_path / "apikeys.json")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_generate_returns_raw_key(tmp_mgr):
    raw = tmp_mgr.generate(label="test", role="operator")
    assert isinstance(raw, str)
    assert len(raw) == 64  # 32-byte hex


def test_verify_valid_key(tmp_mgr):
    raw = tmp_mgr.generate(label="bot", role="viewer")
    role = tmp_mgr.verify(raw)
    assert role == "viewer"


def test_verify_invalid_key(tmp_mgr):
    role = tmp_mgr.verify("not_a_real_key_xyz")
    assert role is None


def test_verify_expired_key(tmp_mgr):
    raw = tmp_mgr.generate(label="expired", role="operator", expires_in_days=-1)
    role = tmp_mgr.verify(raw)
    assert role is None


def test_verify_non_expired(tmp_mgr):
    raw = tmp_mgr.generate(label="valid", role="admin", expires_in_days=30)
    assert tmp_mgr.verify(raw) == "admin"


def test_invalid_role_raises(tmp_mgr):
    with pytest.raises(ValueError, match="Invalid role"):
        tmp_mgr.generate(label="x", role="superuser")


def test_revoke_removes_key(tmp_mgr):
    raw = tmp_mgr.generate(label="r", role="operator")
    listing = tmp_mgr.list()
    key_id = listing[0]["key_id"]
    removed = tmp_mgr.revoke(key_id)
    assert removed is True
    assert tmp_mgr.verify(raw) is None


def test_revoke_unknown_id_returns_false(tmp_mgr):
    assert tmp_mgr.revoke("nonexistent_id") is False


def test_list_excludes_hash(tmp_mgr):
    tmp_mgr.generate(label="l", role="viewer")
    items = tmp_mgr.list()
    assert len(items) == 1
    assert "hash" not in items[0]
    assert "key_id" in items[0]
    assert "label" in items[0]
    assert "role" in items[0]


def test_list_expired_flag(tmp_mgr):
    tmp_mgr.generate(label="exp", role="viewer", expires_in_days=-1)
    items = tmp_mgr.list()
    assert items[0]["expired"] is True


def test_list_sorted_by_creation(tmp_mgr):
    tmp_mgr.generate(label="first", role="viewer")
    time.sleep(0.01)
    tmp_mgr.generate(label="second", role="viewer")
    items = tmp_mgr.list()
    assert items[0]["created_at"] <= items[1]["created_at"]


def test_purge_expired(tmp_mgr):
    tmp_mgr.generate(label="exp1", role="viewer", expires_in_days=-1)
    tmp_mgr.generate(label="exp2", role="viewer", expires_in_days=-1)
    tmp_mgr.generate(label="valid", role="viewer", expires_in_days=30)
    removed = tmp_mgr.purge_expired()
    assert removed == 2
    assert len(tmp_mgr.list()) == 1


def test_persistence(tmp_path):
    from castor.apikeys import ApiKeyManager

    store = tmp_path / "keys.json"
    mgr1 = ApiKeyManager(store_path=store)
    raw = mgr1.generate(label="persist", role="operator")

    mgr2 = ApiKeyManager(store_path=store)
    assert mgr2.verify(raw) == "operator"


def test_get_key_metadata(tmp_mgr):
    tmp_mgr.generate(label="meta", role="admin")
    items = tmp_mgr.list()
    key_id = items[0]["key_id"]
    meta = tmp_mgr.get(key_id)
    assert meta is not None
    assert meta["label"] == "meta"
    assert "hash" not in meta


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTOR_APIKEYS_DB", str(tmp_path / "apikeys.json"))
    import castor.apikeys as m

    m._manager = None  # reset singleton

    from fastapi.testclient import TestClient

    from castor.api import app

    return TestClient(app)


def test_api_keys_generate(api_client):
    resp = api_client.post("/api/keys/generate", json={"label": "ci", "role": "operator"})
    assert resp.status_code == 200
    data = resp.json()
    assert "key" in data
    assert data["role"] == "operator"


def test_api_keys_generate_invalid_role(api_client):
    resp = api_client.post("/api/keys/generate", json={"label": "x", "role": "superuser"})
    assert resp.status_code == 422


def test_api_keys_list(api_client):
    api_client.post("/api/keys/generate", json={"label": "k1", "role": "viewer"})
    resp = api_client.get("/api/keys/list")
    assert resp.status_code == 200
    assert "keys" in resp.json()
    assert len(resp.json()["keys"]) >= 1


def test_api_keys_revoke(api_client):
    api_client.post("/api/keys/generate", json={"label": "revoke_me", "role": "viewer"})
    keys = api_client.get("/api/keys/list").json()["keys"]
    key_id = keys[0]["key_id"]
    resp = api_client.delete(f"/api/keys/{key_id}")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True


def test_api_keys_revoke_not_found(api_client):
    resp = api_client.delete("/api/keys/nonexistent_id_xyz")
    assert resp.status_code == 404
