"""
tests/test_config_history.py — Unit + API tests for castor/config_history.py.

Covers:
  - ConfigHistoryManager: record, list, get, diff, rollback, clear
  - Max versions ring buffer
  - API: GET /api/config/history, POST /api/config/rollback
  - config reload auto-records
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import yaml

_SAMPLE_CONFIG_A = {
    "rcan_version": "1.1.0",
    "metadata": {"robot_name": "bot-a"},
    "agent": {"provider": "google", "model": "gemini-2.5-flash"},
    "drivers": [{"id": "wheels", "protocol": "pca9685"}],
}

_SAMPLE_CONFIG_B = {
    "rcan_version": "1.1.0",
    "metadata": {"robot_name": "bot-b"},
    "agent": {"provider": "ollama", "model": "llama3"},
    "drivers": [{"id": "wheels", "protocol": "pca9685"}],
}


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def hist():
    from castor.config_history import ConfigHistoryManager

    return ConfigHistoryManager(max_versions=5)


def test_record_returns_version_id(hist):
    vid = hist.record(_SAMPLE_CONFIG_A)
    assert vid.startswith("v")
    assert len(vid) > 1


def test_list_newest_first(hist):
    v1 = hist.record(_SAMPLE_CONFIG_A, label="first")
    v2 = hist.record(_SAMPLE_CONFIG_B, label="second")
    listing = hist.list()
    assert listing[0]["version_id"] == v2
    assert listing[1]["version_id"] == v1


def test_list_excludes_config(hist):
    hist.record(_SAMPLE_CONFIG_A)
    for item in hist.list():
        assert "config" not in item


def test_list_includes_summary(hist):
    hist.record(_SAMPLE_CONFIG_A)
    item = hist.list()[0]
    assert "summary" in item
    assert item["summary"]["robot_name"] == "bot-a"


def test_get_returns_full_config(hist):
    vid = hist.record(_SAMPLE_CONFIG_A)
    entry = hist.get(vid)
    assert entry is not None
    assert entry["config"]["metadata"]["robot_name"] == "bot-a"


def test_get_unknown_returns_none(hist):
    assert hist.get("nonexistent_v999") is None


def test_max_versions_ring_buffer(hist):
    for i in range(10):
        hist.record({"rcan_version": str(i), "metadata": {}, "agent": {}, "drivers": []})
    assert len(hist.list()) == 5


def test_diff_detects_change(hist):
    v1 = hist.record(_SAMPLE_CONFIG_A)
    v2 = hist.record(_SAMPLE_CONFIG_B)
    diff = hist.diff(v1, v2)
    assert "-" in diff or "+" in diff  # unified diff has changes
    assert "bot-a" in diff or "bot-b" in diff


def test_diff_unknown_version_raises(hist):
    hist.record(_SAMPLE_CONFIG_A)
    with pytest.raises(ValueError):
        hist.diff("v_bad", "v_also_bad")


def test_rollback_writes_file(hist, tmp_path):
    config_path = str(tmp_path / "robot.rcan.yaml")
    vid = hist.record(_SAMPLE_CONFIG_A, config_path=config_path)
    restored = hist.rollback(vid, config_path=config_path)
    assert restored["metadata"]["robot_name"] == "bot-a"
    with open(config_path) as f:
        on_disk = yaml.safe_load(f)
    assert on_disk["metadata"]["robot_name"] == "bot-a"


def test_rollback_unknown_raises(hist):
    with pytest.raises(ValueError, match="not found"):
        hist.rollback("v_unknown")


def test_clear(hist):
    hist.record(_SAMPLE_CONFIG_A)
    hist.clear()
    assert hist.list() == []


def test_singleton():
    import castor.config_history as m

    m._history = None
    h1 = m.get_history()
    h2 = m.get_history()
    assert h1 is h2
    m._history = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    import castor.config_history as m

    m._history = None

    from fastapi.testclient import TestClient

    from castor.api import app, state

    state.config = _SAMPLE_CONFIG_A.copy()
    return TestClient(app)


def test_api_config_history_empty(api_client):
    resp = api_client.get("/api/config/history")
    assert resp.status_code == 200
    assert "versions" in resp.json()


def test_api_config_history_after_record(api_client):
    from castor.config_history import get_history

    get_history().record(_SAMPLE_CONFIG_A, label="v1")
    resp = api_client.get("/api/config/history")
    assert resp.status_code == 200
    assert len(resp.json()["versions"]) >= 1


def test_api_config_rollback_unknown(api_client):
    resp = api_client.post("/api/config/rollback", json={"version_id": "v_does_not_exist"})
    assert resp.status_code == 404


def test_api_config_rollback_ok(api_client, tmp_path):
    from castor.config_history import get_history

    config_path = str(tmp_path / "r.rcan.yaml")
    with patch.dict(os.environ, {"OPENCASTOR_CONFIG": config_path}):
        vid = get_history().record(_SAMPLE_CONFIG_A, config_path=config_path)
        resp = api_client.post("/api/config/rollback", json={"version_id": vid})
        assert resp.status_code == 200
        data = resp.json()
        assert data["version_id"] == vid
        assert data["status"] == "rolled_back"
