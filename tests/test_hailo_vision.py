"""Tests for Hailo-8 vision module."""

from unittest.mock import MagicMock, patch

import numpy as np


class TestHailoDetection:
    def test_detection_properties(self):
        from castor.hailo_vision import HailoDetection

        d = HailoDetection(0, 0.95, [0.1, 0.2, 0.5, 0.8])
        assert d.class_name == "person"
        assert d.is_obstacle()
        assert abs(d.center_x() - 0.3) < 0.01
        assert abs(d.area() - 0.24) < 0.01

    def test_non_obstacle_class(self):
        from castor.hailo_vision import HailoDetection

        d = HailoDetection(73, 0.8, [0.0, 0.0, 0.1, 0.1])  # book
        assert d.class_name == "book"
        assert not d.is_obstacle()

    def test_unknown_class(self):
        from castor.hailo_vision import HailoDetection

        d = HailoDetection(999, 0.5, [0.0, 0.0, 0.5, 0.5])
        assert d.class_name == "class_999"

    def test_repr(self):
        from castor.hailo_vision import HailoDetection

        d = HailoDetection(0, 0.92, [0, 0, 1, 1])
        assert "person" in repr(d)
        assert "0.92" in repr(d)


class TestHailoVision:
    def test_init_no_hailo(self):
        """Should gracefully degrade when hailo_platform not installed."""
        with patch.dict("sys.modules", {"hailo_platform": None}):
            from castor.hailo_vision import HailoVision

            hv = HailoVision.__new__(HailoVision)
            hv.available = False
            hv._pipeline = None
            assert not hv.available
            assert hv.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == []

    def test_detect_obstacles_empty(self):
        from castor.hailo_vision import HailoVision

        hv = HailoVision.__new__(HailoVision)
        hv.available = False
        hv._pipeline = None
        hv.confidence = 0.4

        result = hv.detect(np.zeros((480, 640, 3), dtype=np.uint8))
        assert result == []

    def test_detect_obstacles_result_structure(self):
        from castor.hailo_vision import HailoVision

        hv = HailoVision.__new__(HailoVision)
        hv.available = True
        hv._pipeline = MagicMock()
        hv._input_name = "input"
        hv._input_hw = (640, 640)
        hv.confidence = 0.4

        # Mock NMS output: person detected
        nms_output = [[] for _ in range(80)]
        nms_output[0] = np.array([[100, 50, 400, 300, 0.95]])  # person
        mock_result = {"output": [[nms_output]]}
        hv._pipeline.infer.return_value = mock_result

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = hv.detect_obstacles(frame)
        assert "obstacles" in result
        assert "clear_path" in result
        assert "all_detections" in result


class TestReactiveLayerHailo:
    def test_hailo_disabled_by_config(self):
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({"reactive": {"hailo_vision": False}})
        assert layer._hailo is None

    def test_hailo_graceful_when_unavailable(self):
        from castor.tiered_brain import ReactiveLayer

        # With hailo_vision=False, should not attempt to load
        layer = ReactiveLayer({"reactive": {"hailo_vision": False}})
        assert layer._hailo is None

    def test_close_releases_hailo(self):
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({"reactive": {"hailo_vision": False}})
        layer._hailo = MagicMock()
        layer.close()
        layer._hailo.close.assert_called_once()
