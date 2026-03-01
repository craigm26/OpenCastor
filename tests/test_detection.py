"""
tests/test_detection.py — Unit tests for castor/detection.py

Covers:
  - Detection dataclass / to_dict()
  - ObjectDetector: mock mode (HAS_YOLO=False, HAS_CV2=False)
  - detect() returns list[Detection]
  - detect_and_annotate() returns bytes (input unchanged when no cv2)
  - latest / latency_ms / mode properties
  - configure() updates threshold and reinitialises model
  - Singleton factory (get_detector)
  - Edge: empty detections list
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_detector(model="auto", conf=0.5):
    """Return an ObjectDetector forced into mock mode."""
    with patch("castor.detection.HAS_YOLO", False), patch("castor.detection.HAS_CV2", False):
        from castor.detection import ObjectDetector

        return ObjectDetector(model_name=model, conf_threshold=conf)


# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


def test_detection_to_dict_keys():
    """Detection.to_dict() must contain class, confidence, bbox, center."""
    from castor.detection import Detection

    d = Detection("person", 0.85, (10, 20, 110, 120))
    out = d.to_dict()
    assert out["class"] == "person"
    assert out["confidence"] == pytest.approx(0.85, abs=0.001)
    assert "bbox" in out
    assert "center" in out


def test_detection_center_calculation():
    """center should be the midpoint of bbox."""
    from castor.detection import Detection

    d = Detection("cat", 0.7, (0, 0, 100, 80))
    out = d.to_dict()
    assert out["center"]["x"] == 50
    assert out["center"]["y"] == 40


def test_detection_bbox_dict_keys():
    """bbox must expose x1, y1, x2, y2."""
    from castor.detection import Detection

    d = Detection("bottle", 0.6, (5, 10, 55, 60))
    bbox = d.to_dict()["bbox"]
    for key in ("x1", "y1", "x2", "y2"):
        assert key in bbox


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def test_detect_returns_list():
    """detect() must return a list."""
    det = _fresh_detector()
    result = det.detect(b"\xff\xd8\xff" + b"\x00" * 50)
    assert isinstance(result, list)


def test_detect_mock_returns_0_to_3_detections():
    """Mock mode returns 0–3 Detection objects."""
    det = _fresh_detector()
    # Run several times; length should always be in [0, 3]
    for _ in range(20):
        result = det.detect(b"")
        assert 0 <= len(result) <= 3


def test_detect_updates_last():
    """After detect(), latest property reflects the result."""
    det = _fresh_detector()
    result = det.detect(b"")
    assert det.latest == [d.to_dict() for d in result]


def test_detect_updates_latency():
    """After detect(), latency_ms is a non-negative float."""
    det = _fresh_detector()
    det.detect(b"")
    assert isinstance(det.latency_ms, float)
    assert det.latency_ms >= 0.0


# ---------------------------------------------------------------------------
# detect_and_annotate()
# ---------------------------------------------------------------------------


def test_detect_and_annotate_no_cv2_returns_input():
    """When HAS_CV2=False, detect_and_annotate() returns the input unchanged."""
    det = _fresh_detector()
    jpeg = b"\xff\xd8\xff" + b"\x00" * 100
    result = det.detect_and_annotate(jpeg)
    assert result == jpeg


def test_detect_and_annotate_empty_bytes_no_crash():
    """detect_and_annotate() with empty bytes must not raise."""
    det = _fresh_detector()
    result = det.detect_and_annotate(b"")
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_mode_is_mock():
    """mode property should be 'mock' in mock environment."""
    det = _fresh_detector()
    assert det.mode == "mock"


def test_latest_initially_empty():
    """Before any detect() call, latest should be an empty list."""
    det = _fresh_detector()
    assert det.latest == []


def test_latency_ms_initially_zero():
    """Before any detect() call, latency_ms should be 0.0."""
    det = _fresh_detector()
    assert det.latency_ms == 0.0


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------


def test_configure_updates_conf_threshold():
    """configure(conf_threshold=...) must update _conf."""
    det = _fresh_detector(conf=0.5)
    det.configure(conf_threshold=0.8)
    assert det._conf == pytest.approx(0.8)


def test_configure_with_same_model_no_reinit():
    """configure() with same model name should NOT reinitialise."""
    det = _fresh_detector()
    original_mode = det.mode
    det.configure(model=det._model_name)  # same name
    assert det.mode == original_mode


def test_configure_model_change_reinitialises():
    """configure() with a different model name should call __init__ again."""
    with patch("castor.detection.HAS_YOLO", False), patch("castor.detection.HAS_CV2", False):
        from castor.detection import ObjectDetector

        det = ObjectDetector(model_name="mock_a", conf_threshold=0.5)
        det.configure(model="mock_b")
        # After reinit the model name should be updated
        assert det._model_name == "mock_b"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_detector_singleton():
    """get_detector() must return the same instance on repeated calls."""
    import castor.detection as det_mod

    det_mod._singleton = None  # reset
    with patch("castor.detection.HAS_YOLO", False), patch("castor.detection.HAS_CV2", False):
        d1 = det_mod.get_detector()
        d2 = det_mod.get_detector()
    assert d1 is d2
    det_mod._singleton = None  # clean up


def test_get_detector_first_call_creates_instance():
    """After resetting singleton, get_detector() creates a fresh instance."""
    import castor.detection as det_mod
    from castor.detection import ObjectDetector

    det_mod._singleton = None
    with patch("castor.detection.HAS_YOLO", False), patch("castor.detection.HAS_CV2", False):
        d = det_mod.get_detector()
    assert isinstance(d, ObjectDetector)
    det_mod._singleton = None
