"""
tests/test_timelapse.py — Unit + API tests for castor/timelapse.py.

Covers:
  - TimelapseGenerator: generate (mock mode), list, get
  - No recordings raises ValueError
  - Index persistence
  - API: POST /api/timelapse/generate, GET /api/timelapse/list
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_MOCK_RECORDINGS = [
    {"id": "rec1", "path": "/fake/rec1.mp4", "duration_s": 10.0},
    {"id": "rec2", "path": "/fake/rec2.mp4", "duration_s": 5.0},
]


def _make_generator(tmp_path):
    from castor.timelapse import TimelapseGenerator

    return TimelapseGenerator(output_dir=tmp_path / "timelapses")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def gen(tmp_path):
    return _make_generator(tmp_path)


@pytest.fixture()
def mock_recorder():
    recorder = MagicMock()
    recorder.list_recordings.return_value = _MOCK_RECORDINGS
    with patch("castor.timelapse.TimelapseGenerator.generate") as _p:
        yield recorder


def test_generate_mock_mode(gen):
    """Generate in mock mode (no cv2 / no real files)."""
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            result = gen.generate(recording_ids=["rec1"], speed_factor=2.0)

    assert "timelapse_id" in result
    assert result["mode"] == "mock"
    assert result["speed_factor"] == 2.0


def test_generate_no_recordings_raises(gen):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = []
        with pytest.raises(ValueError, match="No matching recordings"):
            gen.generate(recording_ids=["nonexistent"])


def test_generate_all_recordings_when_no_ids(gen):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            result = gen.generate(speed_factor=4.0)
    assert set(result["recording_ids"]) == {"rec1", "rec2"}


def test_list_empty_initially(gen):
    assert gen.list() == []


def test_list_after_generate(gen):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            gen.generate()
    listing = gen.list()
    assert len(listing) == 1
    assert "timelapse_id" in listing[0]


def test_list_sorted_newest_first(gen):
    import time

    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            gen.generate()
            time.sleep(0.01)
            gen.generate()
    listing = gen.list()
    assert listing[0]["created_at"] >= listing[1]["created_at"]


def test_get_by_id(gen):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            result = gen.generate()
    tid = result["timelapse_id"]
    assert gen.get(tid) is not None
    assert gen.get("bad_id") is None


def test_index_persists(tmp_path):
    from castor.timelapse import TimelapseGenerator

    g1 = TimelapseGenerator(output_dir=tmp_path / "tl")
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            g1.generate()

    g2 = TimelapseGenerator(output_dir=tmp_path / "tl")
    assert len(g2.list()) == 1


def test_singleton():
    import castor.timelapse as m

    m._generator = None
    g1 = m.get_generator()
    g2 = m.get_generator()
    assert g1 is g2
    m._generator = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path):
    from castor.timelapse import TimelapseGenerator

    fresh_gen = TimelapseGenerator(output_dir=tmp_path / "tl_api")

    with patch("castor.timelapse.get_generator", return_value=fresh_gen):
        from fastapi.testclient import TestClient

        from castor.api import app

        yield TestClient(app)


def test_api_timelapse_list_empty(api_client):
    resp = api_client.get("/api/timelapse/list")
    assert resp.status_code == 200
    assert resp.json()["timelapses"] == []


def test_api_timelapse_generate(api_client):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = _MOCK_RECORDINGS
        with patch("castor.timelapse.HAS_CV2", False):
            resp = api_client.post(
                "/api/timelapse/generate",
                json={"recording_ids": ["rec1"], "speed_factor": 4.0},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert "timelapse_id" in data


def test_api_timelapse_generate_no_recordings(api_client):
    with patch("castor.recorder.get_recorder") as mock_rec:
        mock_rec.return_value = MagicMock()
        mock_rec.return_value.list_recordings.return_value = []
        resp = api_client.post(
            "/api/timelapse/generate",
            json={"recording_ids": ["nonexistent"]},
        )
    assert resp.status_code == 422
