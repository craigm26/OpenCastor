"""
tests/test_finetune.py — Unit + API tests for castor/finetune.py.

Covers:
  - All four export converters (jsonl, alpaca, sharegpt, chatml)
  - EpisodeFinetuneExporter: iter_records, export_to_file, export_to_bytes, stats
  - Filtering: require_action, min_latency_ms
  - Unknown format raises ValueError
  - Convenience export_episodes() function
  - API: GET /api/finetune/export, GET /api/finetune/stats
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_EPISODES = [
    {
        "id": "1",
        "instruction": "go forward",
        "raw_text": '{"action": "forward", "speed": 0.5}',
        "action": {"action": "forward", "speed": 0.5},
        "latency_ms": 120.0,
        "timestamp": "2026-01-01T00:00:00",
        "image_hash": "abc123",
    },
    {
        "id": "2",
        "instruction": "turn left",
        "raw_text": '{"action": "turn", "direction": "left"}',
        "action": None,  # no action
        "latency_ms": 300.0,
        "timestamp": "2026-01-01T00:01:00",
        "image_hash": "def456",
    },
    {
        "id": "3",
        "instruction": "stop",
        "raw_text": '{"action": "stop"}',
        "action": {"action": "stop"},
        "latency_ms": 50.0,
        "timestamp": "2026-01-01T00:02:00",
        "image_hash": "ghi789",
    },
]


def _make_mock_memory(episodes=None):
    mem = MagicMock()
    mem.query_recent.return_value = episodes if episodes is not None else _SAMPLE_EPISODES
    return mem


# ---------------------------------------------------------------------------
# Converter unit tests
# ---------------------------------------------------------------------------


def test_jsonl_converter():
    from castor.finetune import _episode_to_jsonl

    ep = _SAMPLE_EPISODES[0]
    out = _episode_to_jsonl(ep)
    assert out["id"] == "1"
    assert out["instruction"] == "go forward"
    assert out["response"] == ep["raw_text"]
    assert out["action"] == ep["action"]
    assert out["latency_ms"] == 120.0


def test_alpaca_converter():
    from castor.finetune import _episode_to_alpaca

    ep = _SAMPLE_EPISODES[0]
    out = _episode_to_alpaca(ep)
    assert "instruction" in out
    assert out["input"] == "go forward"
    assert out["output"] == ep["raw_text"]


def test_sharegpt_converter():
    from castor.finetune import _episode_to_sharegpt

    ep = _SAMPLE_EPISODES[0]
    out = _episode_to_sharegpt(ep)
    convs = out["conversations"]
    assert convs[0]["from"] == "system"
    assert convs[1]["from"] == "human"
    assert convs[1]["value"] == "go forward"
    assert convs[2]["from"] == "gpt"


def test_chatml_converter():
    from castor.finetune import _episode_to_chatml

    ep = _SAMPLE_EPISODES[0]
    out = _episode_to_chatml(ep)
    msgs = out["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "go forward"
    assert msgs[2]["role"] == "assistant"


# ---------------------------------------------------------------------------
# EpisodeFinetuneExporter tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def exporter():
    from castor.finetune import EpisodeFinetuneExporter

    mem = _make_mock_memory()
    return EpisodeFinetuneExporter(memory=mem)


def test_iter_records_all_formats(exporter):
    for fmt in ("jsonl", "alpaca", "sharegpt", "chatml"):
        records = list(exporter.iter_records(fmt=fmt, limit=100))
        assert len(records) == 3, f"Expected 3 records for fmt={fmt}"


def test_iter_records_unknown_format(exporter):
    with pytest.raises(ValueError, match="Unknown format"):
        list(exporter.iter_records(fmt="parquet"))  # type: ignore[arg-type]


def test_iter_records_require_action(exporter):
    records = list(exporter.iter_records(fmt="jsonl", require_action=True))
    # Episode 2 has action=None → should be skipped
    assert len(records) == 2


def test_iter_records_min_latency_filter(exporter):
    # Episodes with latency_ms > 200 should be skipped (ep2 = 300ms)
    records = list(exporter.iter_records(fmt="jsonl", min_latency_ms=200.0))
    assert len(records) == 2


def test_export_to_file(exporter):
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        count = exporter.export_to_file(path, fmt="chatml", limit=100)
        assert count == 3
        with open(path) as f:
            lines = [json.loads(ln) for ln in f if ln.strip()]
        assert len(lines) == 3
        assert "messages" in lines[0]
    finally:
        os.unlink(path)


def test_export_to_bytes_is_valid_jsonl(exporter):
    data = exporter.export_to_bytes(fmt="alpaca", limit=100)
    assert isinstance(data, bytes)
    lines = [ln for ln in data.decode().splitlines() if ln.strip()]
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert "instruction" in first and "input" in first and "output" in first


def test_stats(exporter):
    s = exporter.stats()
    assert s["total_episodes"] == 3
    assert s["with_action"] == 2
    assert s["without_action"] == 1
    assert "avg_latency_ms" in s
    assert set(s["formats"]) == {"jsonl", "alpaca", "sharegpt", "chatml"}


def test_stats_empty_memory():
    from castor.finetune import EpisodeFinetuneExporter

    exporter = EpisodeFinetuneExporter(memory=_make_mock_memory(episodes=[]))
    s = exporter.stats()
    assert s["total_episodes"] == 0
    assert s["avg_latency_ms"] == 0.0


def test_export_episodes_convenience():
    from castor.finetune import export_episodes

    mem = _make_mock_memory()
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        n = export_episodes(mem, path, fmt="jsonl", limit=100)
        assert n == 3
    finally:
        os.unlink(path)


def test_exporter_lazy_memory_init():
    """EpisodeFinetuneExporter() with no memory arg should create EpisodeMemory."""
    from castor.finetune import EpisodeFinetuneExporter

    with patch("castor.memory.EpisodeMemory") as mock_cls:
        mock_cls.return_value = _make_mock_memory()
        exp = EpisodeFinetuneExporter()
        mock_cls.assert_called_once()
        assert exp._mem is mock_cls.return_value


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    from fastapi.testclient import TestClient

    from castor.api import app

    return TestClient(app)


@pytest.fixture()
def mock_exporter():
    mem = _make_mock_memory()
    with patch("castor.memory.EpisodeMemory") as mock_cls:
        mock_cls.return_value = mem
        yield mem


def test_api_finetune_export_chatml(api_client, mock_exporter):
    resp = api_client.get("/api/finetune/export?format=chatml&limit=100")
    assert resp.status_code == 200
    assert "application" in resp.headers["content-type"]
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert "messages" in first


def test_api_finetune_export_alpaca(api_client, mock_exporter):
    resp = api_client.get("/api/finetune/export?format=alpaca")
    assert resp.status_code == 200
    first = json.loads(resp.text.splitlines()[0])
    assert "instruction" in first


def test_api_finetune_export_invalid_format(api_client):
    resp = api_client.get("/api/finetune/export?format=parquet")
    assert resp.status_code == 422


def test_api_finetune_export_require_action(api_client, mock_exporter):
    resp = api_client.get("/api/finetune/export?format=jsonl&require_action=true")
    assert resp.status_code == 200
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert len(lines) == 2  # ep2 skipped (no action)


def test_api_finetune_stats(api_client, mock_exporter):
    resp = api_client.get("/api/finetune/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_episodes"] == 3
    assert "formats" in data


def test_api_finetune_export_content_disposition(api_client, mock_exporter):
    resp = api_client.get("/api/finetune/export?format=sharegpt")
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "sharegpt" in resp.headers.get("content-disposition", "")


# ===========================================================================
# Issue #216 — HuggingFace Hub upload
# ===========================================================================


class TestFinetuneHubUpload:
    def _make_exporter(self, tmp_path):
        from castor.finetune import EpisodeFinetuneExporter
        from castor.memory import EpisodeMemory

        mem = EpisodeMemory(db_path=str(tmp_path / "upload.db"))
        mem.log_episode(
            instruction="go forward",
            raw_thought='{"type": "move"}',
            action={"type": "move"},
        )
        return EpisodeFinetuneExporter(mem)

    def test_upload_to_hub_returns_ok_dict(self, tmp_path):
        """upload_to_hub() should return {ok, url, records, repo_id}."""
        from unittest.mock import MagicMock, patch

        exporter = self._make_exporter(tmp_path)
        mock_api = MagicMock()
        mock_api.create_repo.return_value = None
        mock_api.upload_file.return_value = None

        with patch("huggingface_hub.HfApi", return_value=mock_api):
            result = exporter.upload_to_hub("testuser/test-dataset", token="faketoken")

        assert result["ok"] is True
        assert "url" in result
        assert result["records"] >= 1
        assert result["repo_id"] == "testuser/test-dataset"
        assert "huggingface.co" in result["url"]

    def test_upload_to_hub_calls_create_repo(self, tmp_path):
        """upload_to_hub() should call HfApi.create_repo with correct args."""
        from unittest.mock import MagicMock, patch

        exporter = self._make_exporter(tmp_path)
        mock_api = MagicMock()

        with patch("huggingface_hub.HfApi", return_value=mock_api):
            exporter.upload_to_hub("testuser/my-dataset", private=False)

        mock_api.create_repo.assert_called_once_with(
            repo_id="testuser/my-dataset",
            repo_type="dataset",
            private=False,
            exist_ok=True,
        )

    def test_upload_to_hub_raises_without_huggingface_hub(self, tmp_path):
        """upload_to_hub() should raise ImportError if huggingface_hub is missing."""
        import builtins
        from unittest.mock import patch

        exporter = self._make_exporter(tmp_path)
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "huggingface_hub":
                raise ImportError("No module named 'huggingface_hub'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="huggingface-hub"):
                exporter.upload_to_hub("testuser/ds")


def test_api_finetune_upload_invalid_format(api_client):
    """POST /api/finetune/upload with invalid format should return 422."""
    resp = api_client.post(
        "/api/finetune/upload",
        json={"repo_id": "user/ds", "fmt": "parquet"},
    )
    assert resp.status_code == 422
