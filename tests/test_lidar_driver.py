"""Tests for castor.drivers.lidar_driver."""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_lidar_singleton():
    import castor.drivers.lidar_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_driver(port="/dev/ttyUSB0"):
    """Return a LidarDriver forced into mock mode (no rplidar)."""
    with patch("castor.drivers.lidar_driver.HAS_RPLIDAR", False):
        from castor.drivers.lidar_driver import LidarDriver

        return LidarDriver(port=port)


# ---------------------------------------------------------------------------
# Init / mock mode
# ---------------------------------------------------------------------------


class TestLidarDriverInit:
    def test_mock_mode_without_rplidar(self):
        drv = _mock_driver()
        assert drv._mode == "mock"

    def test_port_stored(self):
        drv = _mock_driver(port="/dev/ttyUSB1")
        assert drv._port == "/dev/ttyUSB1"

    def test_health_check_ok(self):
        drv = _mock_driver()
        h = drv.health_check()
        assert h["ok"] is True

    def test_health_check_fields(self):
        drv = _mock_driver()
        h = drv.health_check()
        for key in ("ok", "mode", "port", "baud", "scan_count", "error"):
            assert key in h, f"missing key: {key}"


# ---------------------------------------------------------------------------
# scan() — mock data structure
# ---------------------------------------------------------------------------


class TestLidarDriverScan:
    def test_scan_returns_list(self):
        drv = _mock_driver()
        result = drv.scan()
        assert isinstance(result, list)

    def test_scan_360_points(self):
        drv = _mock_driver()
        result = drv.scan()
        assert len(result) == 360

    def test_scan_point_has_required_keys(self):
        drv = _mock_driver()
        result = drv.scan()
        for point in result:
            for key in ("angle_deg", "distance_mm", "quality"):
                assert key in point, f"missing key {key} in point {point}"

    def test_scan_angles_are_numeric(self):
        drv = _mock_driver()
        for point in drv.scan():
            assert isinstance(point["angle_deg"], (int, float))

    def test_scan_distances_positive(self):
        drv = _mock_driver()
        for point in drv.scan():
            assert point["distance_mm"] > 0

    def test_scan_caches_last_scan(self):
        drv = _mock_driver()
        result = drv.scan()
        assert drv._last_scan is result


# ---------------------------------------------------------------------------
# obstacles() — sector analysis
# ---------------------------------------------------------------------------


class TestLidarDriverObstacles:
    def test_obstacles_returns_dict(self):
        drv = _mock_driver()
        obs = drv.obstacles()
        assert isinstance(obs, dict)

    def test_obstacles_has_required_keys(self):
        drv = _mock_driver()
        obs = drv.obstacles()
        assert "min_distance_mm" in obs
        assert "nearest_angle_deg" in obs
        assert "sectors" in obs

    def test_sectors_has_four_directions(self):
        drv = _mock_driver()
        sectors = drv.obstacles()["sectors"]
        for direction in ("front", "right", "rear", "left"):
            assert direction in sectors, f"missing sector: {direction}"

    def test_mock_obstacle_at_90_degrees_in_right_sector(self):
        """Mock generates a 400mm obstacle near 90° which falls in the right sector."""
        drv = _mock_driver()
        obs = drv.obstacles()
        right_dist = obs["sectors"]["right"]
        assert right_dist is not None
        assert right_dist < 500.0  # obstacle at ~400mm

    def test_obstacles_triggers_scan_if_no_last_scan(self):
        drv = _mock_driver()
        assert drv._last_scan == []
        obs = drv.obstacles()
        assert obs["min_distance_mm"] is not None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_noop_in_mock():
    drv = _mock_driver()
    # Should not raise
    drv.start()


def test_stop_noop_when_no_lidar():
    drv = _mock_driver()
    drv.stop()  # should not raise


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_lidar_singleton():
    with patch("castor.drivers.lidar_driver.HAS_RPLIDAR", False):
        from castor.drivers.lidar_driver import get_lidar

        l1 = get_lidar()
        l2 = get_lidar()
    assert l1 is l2


# ---------------------------------------------------------------------------
# _angle_in_sector helper
# ---------------------------------------------------------------------------


def test_angle_in_sector_normal_range():
    from castor.drivers.lidar_driver import _angle_in_sector

    assert _angle_in_sector(90.0, 45.0, 135.0) is True
    assert _angle_in_sector(44.9, 45.0, 135.0) is False


def test_angle_in_sector_wrapping_front():
    from castor.drivers.lidar_driver import _angle_in_sector

    # Front wraps 315-360 + 0-45
    assert _angle_in_sector(350.0, 315.0, 45.0) is True
    assert _angle_in_sector(10.0, 315.0, 45.0) is True
    assert _angle_in_sector(180.0, 315.0, 45.0) is False
