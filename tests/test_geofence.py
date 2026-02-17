"""Tests for castor.geofence -- limit robot operating radius."""

import math
from unittest.mock import patch

from castor.geofence import Geofence


# =====================================================================
# Geofence.__init__
# =====================================================================
class TestGeofenceInit:
    def test_disabled_by_default(self):
        fence = Geofence({})
        assert fence.enabled is False
        assert fence.max_radius == 5.0
        assert fence.action == "stop"

    def test_init_with_config_enabled(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 10.0,
                "action": "warn",
            }
        }
        fence = Geofence(config)
        assert fence.enabled is True
        assert fence.max_radius == 10.0
        assert fence.action == "warn"


# =====================================================================
# Geofence.check_action -- disabled
# =====================================================================
class TestGeofenceCheckDisabled:
    def test_check_action_when_disabled_passes_through(self):
        fence = Geofence({})
        action = {"type": "move", "linear": 100.0, "angular": 0.0}
        result = fence.check_action(action)
        assert result == action


# =====================================================================
# Geofence.check_action -- within bounds
# =====================================================================
class TestGeofenceCheckWithinBounds:
    def test_move_within_bounds_returns_action_unchanged(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 100.0,  # large radius
                "action": "stop",
            }
        }
        fence = Geofence(config)
        action = {"type": "move", "linear": 0.5, "angular": 0.0}
        result = fence.check_action(action)
        assert result == action


# =====================================================================
# Geofence.check_action -- exceeding radius (action=stop)
# =====================================================================
class TestGeofenceCheckExceedStop:
    def test_move_exceeding_radius_returns_stop_action(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 0.01,  # very small radius
                "action": "stop",
            }
        }
        fence = Geofence(config)
        # Large linear speed that will exceed the tiny radius
        action = {"type": "move", "linear": 10.0, "angular": 0.0}
        result = fence.check_action(action)
        assert result == {"type": "stop"}


# =====================================================================
# Geofence.check_action -- exceeding radius (action=warn)
# =====================================================================
class TestGeofenceCheckExceedWarn:
    def test_move_exceeding_radius_logs_warning_and_returns_action(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 0.01,  # very small radius
                "action": "warn",
            }
        }
        fence = Geofence(config)
        action = {"type": "move", "linear": 10.0, "angular": 0.0}
        result = fence.check_action(action)
        # In warn mode, the original action is returned
        assert result == action


# =====================================================================
# Geofence.check_action -- non-move action
# =====================================================================
class TestGeofenceCheckNonMove:
    def test_non_move_action_passes_through(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 0.01,
            }
        }
        fence = Geofence(config)
        action = {"type": "stop"}
        result = fence.check_action(action)
        assert result == action

    def test_none_action_passes_through(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 0.01,
            }
        }
        fence = Geofence(config)
        result = fence.check_action(None)
        assert result is None


# =====================================================================
# Geofence.distance_from_start
# =====================================================================
class TestGeofenceDistance:
    def test_distance_from_start_property(self):
        fence = Geofence({})
        assert fence.distance_from_start == 0.0

        # Simulate movement (disabled fence still tracks position)
        action = {"type": "move", "linear": 2.0, "angular": 0.0}
        fence.check_action(action)
        assert fence.distance_from_start > 0.0


# =====================================================================
# Geofence.position
# =====================================================================
class TestGeofencePosition:
    def test_position_property(self):
        fence = Geofence({})
        x, y = fence.position
        assert x == 0.0
        assert y == 0.0

    def test_position_updates_after_move(self):
        fence = Geofence({})
        action = {"type": "move", "linear": 1.0, "angular": 0.0}
        fence.check_action(action)
        x, y = fence.position
        # Position should have changed from origin
        assert not (x == 0.0 and y == 0.0)


# =====================================================================
# Geofence.reset
# =====================================================================
class TestGeofenceReset:
    def test_reset_returns_to_origin(self):
        fence = Geofence({})
        # Move the robot
        action = {"type": "move", "linear": 5.0, "angular": 0.0}
        fence.check_action(action)
        assert fence.distance_from_start > 0.0

        fence.reset()
        assert fence.distance_from_start == 0.0
        x, y = fence.position
        assert x == 0.0
        assert y == 0.0


# =====================================================================
# Geofence.get_status
# =====================================================================
class TestGeofenceGetStatus:
    def test_get_status_returns_correct_dict(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 5.0,
            }
        }
        fence = Geofence(config)
        status = fence.get_status()

        assert status["enabled"] is True
        assert status["max_radius_m"] == 5.0
        assert status["distance_m"] == 0.0
        assert status["position"]["x"] == 0.0
        assert status["position"]["y"] == 0.0
        assert status["within_bounds"] is True

    def test_get_status_after_exceeding_bounds(self):
        config = {
            "geofence": {
                "enabled": True,
                "max_radius_m": 0.01,
                "action": "warn",  # use warn so position is still updated
            }
        }
        fence = Geofence(config)
        action = {"type": "move", "linear": 10.0, "angular": 0.0}
        fence.check_action(action)

        status = fence.get_status()
        assert status["within_bounds"] is False
        assert status["distance_m"] > 0.01
