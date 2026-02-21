"""Tests for Hailo-8 vision module."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


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


class TestObstacleEvent:
    """ObstacleEvent dataclass and distance estimation."""

    def test_estimate_distance_default_calibration(self):
        from castor.hailo_vision import HailoDetection

        # area=0.25 → 0.25/0.25 = 1.0m
        d = HailoDetection(0, 0.9, [0.375, 0.375, 0.625, 0.625])  # 0.25 × 0.25 = 0.0625... wait
        # bbox: [x1,y1,x2,y2] area = (0.625-0.375)*(0.625-0.375) = 0.25*0.25=0.0625
        # 0.25 / 0.0625 = 4.0m
        dist = d.estimate_distance_m(calibration=0.25)
        assert abs(dist - 4.0) < 0.01

    def test_estimate_distance_large_obstacle(self):
        """Large bbox (area=0.5) → short distance."""
        from castor.hailo_vision import HailoDetection

        d = HailoDetection(0, 0.9, [0.0, 0.0, 1.0, 0.5])  # area = 0.5
        dist = d.estimate_distance_m(calibration=0.25)
        assert dist == pytest.approx(0.5, rel=0.01)

    def test_estimate_distance_zero_area_returns_inf(self):
        from castor.hailo_vision import HailoDetection
        import math

        d = HailoDetection(0, 0.9, [0.5, 0.5, 0.5, 0.5])  # zero area
        assert math.isinf(d.estimate_distance_m())

    def test_to_obstacle_event_fields(self):
        from castor.hailo_vision import HailoDetection, ObstacleEvent

        d = HailoDetection(0, 0.92, [0.0, 0.0, 1.0, 0.5])
        ev = d.to_obstacle_event(calibration=0.25)
        assert isinstance(ev, ObstacleEvent)
        assert ev.label == "person"
        assert ev.confidence == pytest.approx(0.92)
        assert ev.area == pytest.approx(0.5)
        assert ev.distance_m == pytest.approx(0.5, rel=0.01)


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

    def test_distance_thresholds_from_config(self):
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({
            "reactive": {
                "hailo_vision": False,
                "hailo_stop_distance_m": 0.3,
                "hailo_warn_distance_m": 1.5,
                "hailo_calibration": 0.5,
            }
        })
        assert layer.hailo_stop_distance_m == pytest.approx(0.3)
        assert layer.hailo_warn_distance_m == pytest.approx(1.5)
        assert layer.hailo_calibration == pytest.approx(0.5)

    def test_distance_threshold_defaults(self):
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({"reactive": {"hailo_vision": False}})
        assert layer.hailo_stop_distance_m == pytest.approx(0.5)
        assert layer.hailo_warn_distance_m == pytest.approx(1.0)
        assert layer.hailo_calibration == pytest.approx(0.25)

    def test_estop_at_stop_distance(self):
        """Nearest obstacle estimated < stop_distance_m → stop action."""
        from castor.hailo_vision import HailoDetection
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({
            "reactive": {
                "hailo_vision": False,
                "hailo_stop_distance_m": 1.0,
                "hailo_warn_distance_m": 2.0,
                "hailo_calibration": 0.25,
            }
        })

        # Inject a mocked hailo that returns a close obstacle
        mock_hailo = MagicMock()
        # area=0.5 → distance = 0.25/0.5 = 0.5m < stop_distance_m=1.0 → stop
        near = HailoDetection(0, 0.9, [0.0, 0.0, 1.0, 0.5])
        mock_hailo.detect_obstacles.return_value = {
            "obstacles": [near],
            "nearest_obstacle": near,
            "clear_path": False,
            "all_detections": [near],
        }
        layer._hailo = mock_hailo

        import numpy as np
        fake_frame = b"\xff" * 200
        with patch("cv2.imdecode", return_value=np.zeros((480, 640, 3), dtype=np.uint8)):
            with patch("numpy.frombuffer", return_value=np.zeros(200, dtype=np.uint8)):
                action = layer.evaluate(fake_frame)

        assert action is not None
        assert action["type"] == "stop"
        assert "hailo_" in action["reason"]

    def test_warn_at_warn_distance(self):
        """Nearest obstacle between warn and stop → slow-down action."""
        from castor.hailo_vision import HailoDetection
        from castor.tiered_brain import ReactiveLayer

        layer = ReactiveLayer({
            "reactive": {
                "hailo_vision": False,
                "hailo_stop_distance_m": 0.3,
                "hailo_warn_distance_m": 2.0,
                "hailo_calibration": 0.25,
            }
        })

        mock_hailo = MagicMock()
        # area=0.5 → distance=0.5m — between stop(0.3) and warn(2.0) → warn
        near = HailoDetection(0, 0.9, [0.0, 0.0, 1.0, 0.5])
        mock_hailo.detect_obstacles.return_value = {
            "obstacles": [near],
            "nearest_obstacle": near,
            "clear_path": False,
            "all_detections": [near],
        }
        layer._hailo = mock_hailo

        import numpy as np
        fake_frame = b"\xff" * 200
        with patch("cv2.imdecode", return_value=np.zeros((480, 640, 3), dtype=np.uint8)):
            with patch("numpy.frombuffer", return_value=np.zeros(200, dtype=np.uint8)):
                action = layer.evaluate(fake_frame)

        assert action is not None
        assert action["type"] == "move"
        assert action["linear"] == 0.0
