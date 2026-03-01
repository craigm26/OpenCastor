"""Tests for LidarDriver.zone_map() — Issue #325."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_lidar_singleton():
    import castor.drivers.lidar_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helper: build a LidarDriver in mock mode (no rplidar library)
# ---------------------------------------------------------------------------


def _mock_driver():
    """Return a LidarDriver forced into mock mode."""
    with patch("castor.drivers.lidar_driver.HAS_RPLIDAR", False):
        from castor.drivers.lidar_driver import LidarDriver

        return LidarDriver(port="/dev/null")


# ---------------------------------------------------------------------------
# 1. Returns a dict
# ---------------------------------------------------------------------------


def test_zone_map_returns_dict():
    drv = _mock_driver()
    result = drv.zone_map()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 2. Has all required keys
# ---------------------------------------------------------------------------


def test_zone_map_has_required_keys():
    drv = _mock_driver()
    result = drv.zone_map()
    required = {"grid", "width", "height", "resolution_m", "origin", "available"}
    assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# 3. grid is a list of lists
# ---------------------------------------------------------------------------


def test_zone_map_grid_is_list_of_lists():
    drv = _mock_driver()
    result = drv.zone_map()
    assert isinstance(result["grid"], list)
    for row in result["grid"]:
        assert isinstance(row, list)


# ---------------------------------------------------------------------------
# 4. Dimensions match: len(grid) == height, len(grid[0]) == width
# ---------------------------------------------------------------------------


def test_zone_map_dimensions_match():
    drv = _mock_driver()
    result = drv.zone_map()
    assert len(result["grid"]) == result["height"]
    if result["height"] > 0:
        assert len(result["grid"][0]) == result["width"]


# ---------------------------------------------------------------------------
# 5. Default resolution_m is 0.05
# ---------------------------------------------------------------------------


def test_zone_map_default_resolution():
    drv = _mock_driver()
    result = drv.zone_map()
    assert result["resolution_m"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# 6. Custom resolution_m is reflected in result
# ---------------------------------------------------------------------------


def test_zone_map_custom_resolution():
    drv = _mock_driver()
    result = drv.zone_map(resolution_m=0.10)
    assert result["resolution_m"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# 7. Origin is at the centre of the grid
# ---------------------------------------------------------------------------


def test_zone_map_origin_is_center():
    drv = _mock_driver()
    result = drv.zone_map()
    w = result["width"]
    h = result["height"]
    ox = result["origin"]["x"]
    oy = result["origin"]["y"]
    assert ox == w // 2
    assert oy == h // 2


# ---------------------------------------------------------------------------
# 8. All grid values are in {-1, 0, 100}
# ---------------------------------------------------------------------------


def test_zone_map_grid_values_valid():
    drv = _mock_driver()
    result = drv.zone_map()
    valid = {-1, 0, 100}
    for row in result["grid"]:
        for cell in row:
            assert cell in valid, f"Unexpected grid value: {cell}"


# ---------------------------------------------------------------------------
# 9. available=False when scan() raises
# ---------------------------------------------------------------------------


def test_zone_map_unavailable_when_scan_fails():
    drv = _mock_driver()

    def _fail_scan():
        raise RuntimeError("simulated scan failure")

    drv.scan = _fail_scan
    result = drv.zone_map()
    assert isinstance(result, dict)
    assert result["available"] is False


# ---------------------------------------------------------------------------
# 10. No crash when scan returns None distances
# ---------------------------------------------------------------------------


def test_zone_map_no_crash_with_none_distances():
    drv = _mock_driver()

    # Patch scan to return points where distance_mm is None
    def _scan_with_nones():
        return [
            {"angle_deg": 0.0, "distance_mm": None, "quality": 10},
            {"angle_deg": 90.0, "distance_mm": None, "quality": 10},
            {"angle_deg": 180.0, "distance_mm": 1500.0, "quality": 15},
        ]

    drv.scan = _scan_with_nones
    result = drv.zone_map()
    assert isinstance(result, dict)
    # Must not raise and must return a valid grid
    assert "grid" in result


# ---------------------------------------------------------------------------
# 11. Very large size_m produces a grid no larger than 200x200
# ---------------------------------------------------------------------------


def test_zone_map_size_m_cap():
    drv = _mock_driver()
    # size_m=1000, resolution_m=0.05 → would be 20000x20000 without cap
    result = drv.zone_map(resolution_m=0.05, size_m=1000.0)
    assert result["width"] <= 200
    assert result["height"] <= 200


# ---------------------------------------------------------------------------
# 12. available=True when scan returns data
# ---------------------------------------------------------------------------


def test_zone_map_available_true_when_scan_ok():
    drv = _mock_driver()

    # Patch scan to return a well-formed scan with one data point
    def _good_scan():
        return [
            {"angle_deg": 0.0, "distance_mm": 1000.0, "quality": 15},
            {"angle_deg": 90.0, "distance_mm": 800.0, "quality": 15},
        ]

    drv.scan = _good_scan
    result = drv.zone_map()
    assert result["available"] is True
