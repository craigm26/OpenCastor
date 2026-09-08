"""Mock mode on the PCA9685 drivers is an opt-in, not a fallback.

The old behaviour answered every failure by setting `self.pca = None`, logging
"Falling back to mock mode", and then accepting move() calls forever. Nothing
above the driver could tell that apart from a working one, so the symptom the
owner saw was a stick that moves and a car that does not. These tests pin the
new contract: a failure raises, unless somebody asked for a pretend robot.
"""

from __future__ import annotations

import pytest

from castor.drivers.pca9685 import (
    ALLOW_MOCK_ENV,
    PCA9685Driver,
    PCA9685RCDriver,
    PCA9685Unavailable,
    _mock_allowed,
)

DRIVERS = [PCA9685RCDriver, PCA9685Driver]


@pytest.fixture(autouse=True)
def _no_ambient_opt_in(monkeypatch):
    """A developer's shell must not decide whether a car pretends to move."""
    monkeypatch.delenv(ALLOW_MOCK_ENV, raising=False)


@pytest.mark.parametrize("cls", DRIVERS)
def test_missing_hardware_raises_instead_of_mocking(cls):
    with pytest.raises(PCA9685Unavailable):
        cls({})


@pytest.mark.parametrize("cls", DRIVERS)
def test_the_error_names_the_surface_that_actually_drives(cls):
    with pytest.raises(PCA9685Unavailable) as exc:
        cls({})
    message = str(exc.value)
    assert "rc-car-actuator" in message
    assert "gateway-policy.env" in message
    assert ALLOW_MOCK_ENV in message  # and how to opt in anyway


@pytest.mark.parametrize("cls", DRIVERS)
def test_config_flag_opts_in(cls):
    driver = cls({"allow_mock": True})
    assert driver.pca is None


@pytest.mark.parametrize("cls", DRIVERS)
def test_env_var_opts_in(cls, monkeypatch):
    monkeypatch.setenv(ALLOW_MOCK_ENV, "1")
    assert cls({}).pca is None


@pytest.mark.parametrize("cls", DRIVERS)
def test_config_flag_false_beats_the_environment(cls, monkeypatch):
    """An explicit `allow_mock: false` in the config is a decision, not a default."""
    monkeypatch.setenv(ALLOW_MOCK_ENV, "1")
    with pytest.raises(PCA9685Unavailable):
        cls({"allow_mock": False})


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("maybe", False),
])
def test_env_opt_in_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(ALLOW_MOCK_ENV, value)
    assert _mock_allowed({}) is expected


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), ("true", True), ("no", False), (1, True), (0, False),
])
def test_config_opt_in_parsing(value, expected):
    assert _mock_allowed({"allow_mock": value}) is expected


def test_a_mocked_driver_still_behaves_as_before():
    """Opting in must not change what mock mode does, only who asks for it."""
    driver = PCA9685RCDriver({"allow_mock": True})
    driver.move(linear_x=0.5, angular_z=0.0)  # no hardware, no raise
    driver.stop()
    driver.close()
    assert driver.health_check() == {
        "ok": False,
        "mode": "mock",
        "error": "PCA9685 unavailable (mock mode)",
    }


def test_unavailable_is_a_runtime_error():
    """So a caller that only catches RuntimeError still fails closed."""
    assert issubclass(PCA9685Unavailable, RuntimeError)


# ── the config-verifier still runs on a host without the hardware ────────────


def test_setup_verify_downgrades_an_unconstructable_driver_to_a_warning():
    """Authoring a Pi config on a laptop must not become a blocking error.

    `verify_setup_config` builds the driver as a DRY RUN of the factory. Once
    the PCA9685 drivers stopped mocking silently, that call started raising on
    every host without the Adafruit libraries — which is most hosts somebody
    writes a config on. The honest answer is a warning about THIS machine.
    """
    from unittest.mock import MagicMock, patch

    from castor import setup_service

    with patch("castor.setup_service.generate_setup_config") as mock_gen:
        mock_gen.return_value = {
            "filename": "verifybot.rcan.yaml",
            "agent_config": {"provider": "ollama", "model": "llava:13b", "env_var": None},
            "config": {"drivers": [{"protocol": "pca9685_i2c"}], "channels": []},
        }
        fake_provider = MagicMock()
        fake_provider.health_check.return_value = {"ok": True}
        with patch("castor.providers.get_provider", return_value=fake_provider):
            result = setup_service.verify_setup_config(
                robot_name="VerifyBot",
                provider="ollama",
                model="llava:13b",
                preset="rpi_rc_car",
                allow_warnings=True,
            )

    assert result["blocking_errors"] == []
    assert any("could not be constructed" in w for w in result["warnings"])
