"""Tests for Episode dataclass."""

import time
import uuid

from castor.learner.episode import Episode


class TestEpisodeCreation:
    def test_default_values(self):
        ep = Episode()
        assert ep.goal == ""
        assert ep.actions == []
        assert ep.sensor_readings == []
        assert ep.success is False
        assert ep.duration_s == 0.0
        assert ep.metadata == {}
        assert ep.id  # should have a UUID

    def test_custom_values(self):
        ep = Episode(goal="pick cup", success=True, duration_s=5.0)
        assert ep.goal == "pick cup"
        assert ep.success is True
        assert ep.duration_s == 5.0

    def test_id_is_valid_uuid(self):
        ep = Episode()
        uuid.UUID(ep.id)  # should not raise

    def test_unique_ids(self):
        eps = [Episode() for _ in range(10)]
        ids = [e.id for e in eps]
        assert len(set(ids)) == 10

    def test_start_time_default(self):
        before = time.time()
        ep = Episode()
        after = time.time()
        assert before <= ep.start_time <= after

    def test_custom_metadata(self):
        ep = Episode(metadata={"robot": "arm1", "env": "lab"})
        assert ep.metadata["robot"] == "arm1"
        assert ep.metadata["env"] == "lab"


class TestEpisodeSerialization:
    def test_to_dict_keys(self):
        ep = Episode(goal="test")
        d = ep.to_dict()
        expected_keys = {
            "id",
            "goal",
            "actions",
            "sensor_readings",
            "success",
            "duration_s",
            "start_time",
            "end_time",
            "metadata",
        }
        assert set(d.keys()) == expected_keys

    def test_roundtrip(self):
        ep = Episode(
            goal="navigate to dock",
            actions=[{"type": "move", "result": {"success": True}}],
            sensor_readings=[{"lidar": 1.5}],
            success=True,
            duration_s=12.3,
            start_time=1000.0,
            end_time=1012.3,
            metadata={"trial": 1},
        )
        restored = Episode.from_dict(ep.to_dict())
        assert restored.id == ep.id
        assert restored.goal == ep.goal
        assert restored.actions == ep.actions
        assert restored.sensor_readings == ep.sensor_readings
        assert restored.success == ep.success
        assert restored.duration_s == ep.duration_s
        assert restored.metadata == ep.metadata

    def test_from_dict_defaults(self):
        ep = Episode.from_dict({})
        assert ep.goal == ""
        assert ep.success is False

    def test_from_dict_partial(self):
        ep = Episode.from_dict({"goal": "hello", "success": True})
        assert ep.goal == "hello"
        assert ep.success is True
        assert ep.actions == []

    def test_actions_preserved(self):
        actions = [
            {"type": "grasp", "result": {"success": False, "error": "slip"}},
            {"type": "move", "result": {"success": True}},
        ]
        ep = Episode(actions=actions)
        restored = Episode.from_dict(ep.to_dict())
        assert restored.actions == actions
