"""Tests for IMUDriver.vibration_bands() — Issue #324."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_imu_singleton():
    import castor.drivers.imu_driver as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helper: build a driver in guaranteed mock mode (no smbus2)
# ---------------------------------------------------------------------------


def _mock_driver():
    """Return an IMUDriver forced into mock mode."""
    with patch("castor.drivers.imu_driver.HAS_SMBUS2", False):
        from castor.drivers.imu_driver import IMUDriver

        return IMUDriver(bus=1, model="auto")


# ---------------------------------------------------------------------------
# 1. Returns a dict
# ---------------------------------------------------------------------------


def test_vibration_bands_returns_dict():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 2. Has all required keys
# ---------------------------------------------------------------------------


def test_vibration_bands_has_required_keys():
    drv = _mock_driver()
    result = drv.vibration_bands()
    required = {"dominant_hz", "bands", "rms_g", "samples", "alert"}
    assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# 3. dominant_hz is a float
# ---------------------------------------------------------------------------


def test_vibration_bands_dominant_hz_is_float():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert isinstance(result["dominant_hz"], float)


# ---------------------------------------------------------------------------
# 4. bands dict has three keys: low, mid, high
# ---------------------------------------------------------------------------


def test_vibration_bands_bands_has_three_keys():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert isinstance(result["bands"], dict)
    assert set(result["bands"].keys()) == {"low", "mid", "high"}


# ---------------------------------------------------------------------------
# 5. rms_g is non-negative
# ---------------------------------------------------------------------------


def test_vibration_bands_rms_g_non_negative():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert result["rms_g"] >= 0.0


# ---------------------------------------------------------------------------
# 6. samples is non-negative
# ---------------------------------------------------------------------------


def test_vibration_bands_samples_non_negative():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert result["samples"] >= 0


# ---------------------------------------------------------------------------
# 7. alert is a bool
# ---------------------------------------------------------------------------


def test_vibration_bands_alert_is_bool():
    drv = _mock_driver()
    result = drv.vibration_bands()
    assert isinstance(result["alert"], bool)


# ---------------------------------------------------------------------------
# 8. alert is False when rms_g is below the threshold
# ---------------------------------------------------------------------------


def test_vibration_bands_alert_false_below_threshold(monkeypatch):
    """With a very high threshold, alert must be False."""
    monkeypatch.setenv("IMU_VIBRATION_THRESHOLD_G", "999.0")
    drv = _mock_driver()
    result = drv.vibration_bands(window_n=8)
    assert result["alert"] is False


# ---------------------------------------------------------------------------
# 9. alert is True when rms_g exceeds the threshold
# ---------------------------------------------------------------------------


def test_vibration_bands_alert_true_above_threshold(monkeypatch):
    """With a near-zero threshold, alert must be True (mock data has rms ~1 g)."""
    monkeypatch.setenv("IMU_VIBRATION_THRESHOLD_G", "0.0001")
    drv = _mock_driver()
    result = drv.vibration_bands(window_n=8)
    # Mock data returns accel_g z ~ 1.0, so rms_g > 0.0001
    assert result["alert"] is True


# ---------------------------------------------------------------------------
# 10. Mock mode (read() always failing) does not crash; returns valid dict
# ---------------------------------------------------------------------------


def test_vibration_bands_mock_mode_no_crash():
    """Even when read() raises every time, vibration_bands must not raise."""
    drv = _mock_driver()

    def _fail_read():
        raise RuntimeError("simulated sensor failure")

    drv.read = _fail_read
    result = drv.vibration_bands(window_n=4)
    assert isinstance(result, dict)
    assert "dominant_hz" in result
    assert "bands" in result
    assert "rms_g" in result
    assert "samples" in result
    assert "alert" in result
    # All samples failed, so either samples==0 (zero-dict path) or dict is valid
    assert result["rms_g"] >= 0.0


# ---------------------------------------------------------------------------
# 11. samples <= window_n
# ---------------------------------------------------------------------------


def test_vibration_bands_window_n_respected():
    drv = _mock_driver()
    window = 16
    result = drv.vibration_bands(window_n=window)
    assert result["samples"] <= window


# ---------------------------------------------------------------------------
# 12. IMU_VIBRATION_THRESHOLD_G env var is read
# ---------------------------------------------------------------------------


def test_vibration_threshold_from_env(monkeypatch):
    """Setting IMU_VIBRATION_THRESHOLD_G to 0 makes alert True for any signal."""
    monkeypatch.setenv("IMU_VIBRATION_THRESHOLD_G", "0.0")
    drv = _mock_driver()
    result = drv.vibration_bands(window_n=8)
    # rms_g will be > 0 in mock mode, so alert should be True
    assert result["alert"] is True
