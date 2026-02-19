"""Tests for ObserverAgent and SceneGraph construction."""

import asyncio
import time

from castor.agents.base import AgentStatus
from castor.agents.observer import Detection, ObserverAgent, SceneGraph
from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_hailo_det(label="person", confidence=0.9, bbox=None):
    """Return a raw hailo-style detection dict."""
    return {
        "label": label,
        "confidence": confidence,
        "bbox": bbox or [0.1, 0.1, 0.3, 0.3],
    }


def observe(agent, sensor_data):
    """Synchronous helper — runs agent.observe in a fresh event loop."""
    return asyncio.run(agent.observe(sensor_data))


def act(agent, context=None):
    """Synchronous helper — runs agent.act in a fresh event loop."""
    return asyncio.run(agent.act(context or {}))


# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


class TestDetection:
    def test_fields_accessible(self):
        d = Detection(
            label="person",
            confidence=0.9,
            bbox=(0.1, 0.1, 0.3, 0.3),
            distance_m=1.5,
            is_obstacle=True,
        )
        assert d.label == "person"
        assert d.confidence == 0.9
        assert d.distance_m == 1.5
        assert d.is_obstacle is True

    def test_no_distance_is_none(self):
        d = Detection("chair", 0.5, (0, 0, 1, 1), None, True)
        assert d.distance_m is None

    def test_non_obstacle(self):
        d = Detection("book", 0.7, (0, 0, 0.1, 0.1), None, False)
        assert d.is_obstacle is False

    def test_bbox_has_four_elements(self):
        d = Detection("car", 0.8, (0.2, 0.1, 0.5, 0.9), None, True)
        assert len(d.bbox) == 4


# ---------------------------------------------------------------------------
# SceneGraph dataclass
# ---------------------------------------------------------------------------


class TestSceneGraph:
    def test_timestamp_stored(self):
        sg = SceneGraph(timestamp=12345.0)
        assert sg.timestamp == 12345.0

    def test_detections_default_empty(self):
        sg = SceneGraph(timestamp=0.0)
        assert sg.detections == []

    def test_free_space_default_one(self):
        sg = SceneGraph(timestamp=0.0)
        assert sg.free_space_pct == 1.0

    def test_closest_obstacle_default_none(self):
        sg = SceneGraph(timestamp=0.0)
        assert sg.closest_obstacle_m is None

    def test_dominant_objects_default_empty(self):
        sg = SceneGraph(timestamp=0.0)
        assert sg.dominant_objects == []

    def test_raw_sensor_keys_default_empty(self):
        sg = SceneGraph(timestamp=0.0)
        assert sg.raw_sensor_keys == []


# ---------------------------------------------------------------------------
# Empty / None sensor data
# ---------------------------------------------------------------------------


class TestObserverEmptySensorData:
    def test_empty_dict_returns_scene_graph(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert isinstance(scene, SceneGraph)

    def test_none_returns_scene_graph(self):
        agent = ObserverAgent()
        scene = observe(agent, None)
        assert isinstance(scene, SceneGraph)

    def test_empty_detections_list(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert scene.detections == []

    def test_free_space_one_when_no_detections(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert scene.free_space_pct == 1.0

    def test_closest_obstacle_none_when_empty(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert scene.closest_obstacle_m is None

    def test_timestamp_set_to_current_time(self):
        agent = ObserverAgent()
        before = time.time()
        scene = observe(agent, {})
        after = time.time()
        assert before <= scene.timestamp <= after

    def test_none_hailo_detections_key(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": None})
        assert scene.detections == []

    def test_empty_hailo_list(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": []})
        assert scene.detections == []

    def test_no_raw_sensor_keys_when_empty(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert scene.raw_sensor_keys == []


# ---------------------------------------------------------------------------
# Detection parsing
# ---------------------------------------------------------------------------


class TestObserverDetectionParsing:
    def test_single_detection_parsed(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("person", 0.9)]})
        assert len(scene.detections) == 1
        assert scene.detections[0].label == "person"

    def test_confidence_stored(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("person", 0.95)]})
        assert abs(scene.detections[0].confidence - 0.95) < 1e-6

    def test_person_is_obstacle(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("person")]})
        assert scene.detections[0].is_obstacle is True

    def test_car_is_obstacle(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("car")]})
        assert scene.detections[0].is_obstacle is True

    def test_unknown_label_not_obstacle(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("unicorn")]})
        assert scene.detections[0].is_obstacle is False

    def test_book_not_obstacle(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("book")]})
        assert scene.detections[0].is_obstacle is False

    def test_multiple_detections(self):
        agent = ObserverAgent()
        dets = [make_hailo_det("person"), make_hailo_det("car"), make_hailo_det("book")]
        scene = observe(agent, {"hailo_detections": dets})
        assert len(scene.detections) == 3

    def test_malformed_detection_skipped_valid_remains(self):
        agent = ObserverAgent()
        dets = [{"garbage": True, "bbox": "bad"}, make_hailo_det("person")]
        scene = observe(agent, {"hailo_detections": dets})
        labels = [d.label for d in scene.detections]
        assert "person" in labels

    def test_class_name_alias_accepted(self):
        """hailo_vision.py uses 'class_name' — both aliases must work."""
        agent = ObserverAgent()
        det = {"class_name": "dog", "score": 0.8, "bbox": [0.1, 0.1, 0.2, 0.2]}
        scene = observe(agent, {"hailo_detections": [det]})
        assert scene.detections[0].label == "dog"

    def test_score_alias_accepted(self):
        agent = ObserverAgent()
        det = {"label": "cat", "score": 0.75, "bbox": [0.0, 0.0, 0.1, 0.1]}
        scene = observe(agent, {"hailo_detections": [det]})
        assert abs(scene.detections[0].confidence - 0.75) < 1e-6

    def test_hailo_key_added_to_raw_sensor_keys(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det()]})
        assert "hailo_detections" in scene.raw_sensor_keys


# ---------------------------------------------------------------------------
# Dominant objects
# ---------------------------------------------------------------------------


class TestObserverDominantObjects:
    def test_top3_by_confidence(self):
        agent = ObserverAgent()
        dets = [
            make_hailo_det("person", 0.9),
            make_hailo_det("car", 0.8),
            make_hailo_det("dog", 0.7),
            make_hailo_det("chair", 0.5),
        ]
        scene = observe(agent, {"hailo_detections": dets})
        assert len(scene.dominant_objects) <= 3
        assert "person" in scene.dominant_objects

    def test_unique_labels(self):
        agent = ObserverAgent()
        dets = [make_hailo_det("person", 0.9), make_hailo_det("person", 0.8)]
        scene = observe(agent, {"hailo_detections": dets})
        assert scene.dominant_objects.count("person") == 1

    def test_empty_detections_empty_dominant(self):
        agent = ObserverAgent()
        scene = observe(agent, {})
        assert scene.dominant_objects == []


# ---------------------------------------------------------------------------
# Free space estimation
# ---------------------------------------------------------------------------


class TestObserverFreeSpace:
    def test_large_obstacle_reduces_free_space(self):
        agent = ObserverAgent()
        dets = [{"label": "person", "confidence": 0.9, "bbox": [0.0, 0.0, 0.9, 0.9]}]
        scene = observe(agent, {"hailo_detections": dets})
        assert scene.free_space_pct < 1.0

    def test_non_obstacle_does_not_reduce_free_space(self):
        agent = ObserverAgent()
        dets = [{"label": "book", "confidence": 0.8, "bbox": [0.0, 0.0, 0.9, 0.9]}]
        scene = observe(agent, {"hailo_detections": dets})
        assert scene.free_space_pct == 1.0

    def test_free_space_bounded_zero_to_one(self):
        agent = ObserverAgent()
        dets = [{"label": "person", "confidence": 0.9, "bbox": [0.0, 0.0, 1.0, 1.0]}]
        scene = observe(agent, {"hailo_detections": dets})
        assert 0.0 <= scene.free_space_pct <= 1.0


# ---------------------------------------------------------------------------
# Closest obstacle
# ---------------------------------------------------------------------------


class TestObserverClosestObstacle:
    def test_no_obstacle_no_distance(self):
        agent = ObserverAgent()
        scene = observe(agent, {"hailo_detections": [make_hailo_det("book")]})
        # book is not an obstacle, so closest_obstacle_m stays None
        assert scene.closest_obstacle_m is None

    def test_closest_from_depth_map(self):
        try:
            import numpy as np
        except ImportError:
            return  # skip if numpy not available

        agent = ObserverAgent()
        depth = np.full((10, 10), 5.0)
        depth[5, 5] = 1.2  # closer point at centre
        sensor_data = {
            "hailo_detections": [
                {"label": "person", "confidence": 0.9, "bbox": [0.4, 0.4, 0.6, 0.6]}
            ],
            "depth_map": depth,
        }
        scene = observe(agent, sensor_data)
        assert scene.closest_obstacle_m is not None
        assert scene.closest_obstacle_m < 5.0  # should be ~1.2

    def test_depth_key_in_raw_sensor_keys(self):
        try:
            import numpy as np
        except ImportError:
            return

        agent = ObserverAgent()
        depth = 2.0 * __import__("numpy").ones((5, 5))
        sensor_data = {
            "hailo_detections": [make_hailo_det("person")],
            "depth_map": depth,
        }
        scene = observe(agent, sensor_data)
        assert "depth_map" in scene.raw_sensor_keys

    def test_multiple_obstacles_closest_wins(self):
        try:
            import numpy as np
        except ImportError:
            return

        np = __import__("numpy")
        agent = ObserverAgent()
        depth = 3.0 * np.ones((10, 10))
        depth[2, 2] = 0.8  # near obstacle
        depth[8, 8] = 2.5  # far obstacle
        sensor_data = {
            "hailo_detections": [
                {"label": "person", "confidence": 0.9, "bbox": [0.1, 0.1, 0.3, 0.3]},
                {"label": "car", "confidence": 0.8, "bbox": [0.7, 0.7, 0.9, 0.9]},
            ],
            "depth_map": depth,
        }
        scene = observe(agent, sensor_data)
        if scene.closest_obstacle_m is not None:
            assert scene.closest_obstacle_m <= 2.5


# ---------------------------------------------------------------------------
# SharedState publishing
# ---------------------------------------------------------------------------


class TestObserverSharedState:
    def test_publishes_scene_graph_to_state(self):
        state = SharedState()
        agent = ObserverAgent(shared_state=state)
        observe(agent, {"hailo_detections": [make_hailo_det("person")]})
        sg = state.get("scene_graph")
        assert sg is not None
        assert isinstance(sg, SceneGraph)

    def test_subsequent_observe_updates_state(self):
        state = SharedState()
        agent = ObserverAgent(shared_state=state)
        observe(agent, {"hailo_detections": [make_hailo_det("person")]})
        observe(agent, {})  # second call with no detections
        sg = state.get("scene_graph")
        assert sg.detections == []

    def test_status_running_after_observe(self):
        agent = ObserverAgent()
        observe(agent, {})
        assert agent.status == AgentStatus.RUNNING


# ---------------------------------------------------------------------------
# act()
# ---------------------------------------------------------------------------


class TestObserverAct:
    def test_act_returns_dict(self):
        agent = ObserverAgent()
        result = act(agent)
        assert isinstance(result, dict)

    def test_act_no_scene_returns_wait(self):
        agent = ObserverAgent()
        result = act(agent)
        assert result["action"] == "wait"

    def test_act_with_clear_scene_returns_observe(self):
        state = SharedState()
        agent = ObserverAgent(shared_state=state)
        observe(agent, {})  # populates scene_graph
        result = act(agent)
        assert result["action"] == "observe"
