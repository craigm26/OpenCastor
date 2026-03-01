"""Regression tests for ESP32 + LEGO runtime support."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from castor.drivers import get_driver
from castor.test_hardware import run_test


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _preset_path(name: str) -> str:
    return str(_repo_root() / "config" / "presets" / f"{name}.rcan.yaml")


def _force_ev3_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import castor.drivers.ev3dev_driver as ev3_mod

    monkeypatch.setattr(ev3_mod, "HAS_EV3DEV2", False)
    monkeypatch.setattr(ev3_mod.shutil, "which", lambda _name: None)


def _force_spike_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import castor.drivers.spike_driver as spike_mod

    monkeypatch.setattr(spike_mod, "HAS_SERIAL", False)
    monkeypatch.setattr(spike_mod, "HAS_BLEAK", False)


def test_driver_factory_resolves_new_protocols(monkeypatch: pytest.MonkeyPatch):
    from castor.drivers.esp32_websocket import ESP32WebsocketDriver
    from castor.drivers.ev3dev_driver import EV3DevDriver
    from castor.drivers.spike_driver import SpikeHubDriver

    _force_ev3_mock_mode(monkeypatch)
    _force_spike_mock_mode(monkeypatch)

    esp32 = get_driver({"drivers": [{"protocol": "esp32_websocket", "host": ""}]})
    ev3 = get_driver(
        {
            "drivers": [
                {"id": "left_motor", "protocol": "ev3dev_tacho_motor", "port": "outA"},
                {"id": "right_motor", "protocol": "ev3dev_tacho_motor", "port": "outD"},
            ],
            "connection": {"host": ""},
        }
    )
    spike = get_driver(
        {
            "drivers": [
                {
                    "id": "left_motor",
                    "protocol": "spike_hub_serial",
                    "port": "A",
                    "device": "motor",
                },
                {
                    "id": "right_motor",
                    "protocol": "spike_hub_serial",
                    "port": "B",
                    "device": "motor",
                },
            ],
            "connection": {"port": "COM_TEST"},
        }
    )

    assert isinstance(esp32, ESP32WebsocketDriver)
    assert isinstance(ev3, EV3DevDriver)
    assert isinstance(spike, SpikeHubDriver)


def test_new_drivers_support_move_stop_close_and_health(monkeypatch: pytest.MonkeyPatch):
    from castor.drivers.esp32_websocket import ESP32WebsocketDriver
    from castor.drivers.ev3dev_driver import EV3DevDriver
    from castor.drivers.spike_driver import SpikeHubDriver

    _force_ev3_mock_mode(monkeypatch)
    _force_spike_mock_mode(monkeypatch)

    drivers = [
        ESP32WebsocketDriver({"host": ""}),
        EV3DevDriver(
            {
                "drivers": [
                    {"id": "left_motor", "protocol": "ev3dev_tacho_motor", "port": "outA"},
                    {"id": "right_motor", "protocol": "ev3dev_tacho_motor", "port": "outD"},
                ],
                "connection": {"host": ""},
            }
        ),
        SpikeHubDriver(
            {
                "drivers": [
                    {
                        "id": "left_motor",
                        "protocol": "spike_hub_serial",
                        "port": "A",
                        "device": "motor",
                    },
                    {
                        "id": "right_motor",
                        "protocol": "spike_hub_serial",
                        "port": "B",
                        "device": "motor",
                    },
                ],
                "connection": {"port": "COM_TEST"},
            }
        ),
    ]

    for driver in drivers:
        driver.move(0.1, 0.0)
        driver.stop()
        health = driver.health_check()
        assert isinstance(health, dict)
        assert "ok" in health
        assert "mode" in health
        driver.close()


@pytest.mark.parametrize(
    "preset_name",
    ["esp32_generic", "lego_mindstorms_ev3", "lego_spike_prime"],
)
def test_test_hardware_runs_for_new_presets(monkeypatch: pytest.MonkeyPatch, preset_name: str):
    import castor.test_hardware as test_hw
    from castor.drivers.esp32_websocket import ESP32WebsocketDriver

    _force_ev3_mock_mode(monkeypatch)
    _force_spike_mock_mode(monkeypatch)
    monkeypatch.setattr(test_hw.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ESP32WebsocketDriver, "move", lambda self, linear=0.0, angular=0.0: None)
    monkeypatch.setattr(ESP32WebsocketDriver, "stop", lambda self: None)
    monkeypatch.setattr(ESP32WebsocketDriver, "close", lambda self: None)

    assert run_test(_preset_path(preset_name), skip_confirm=True) is True


def test_tutorial_page_has_reveal_observer_wiring():
    tutorial_path = _repo_root() / "site" / "tutorials.html"
    html = tutorial_path.read_text(encoding="utf-8")
    assert "IntersectionObserver" in html
    assert "document.querySelectorAll('.reveal').forEach(el => ro.observe(el));" in html


def test_esp32_preset_no_longer_references_missing_firmware_paths():
    preset_path = _repo_root() / "config" / "presets" / "esp32_generic.rcan.yaml"
    data = yaml.safe_load(preset_path.read_text(encoding="utf-8"))
    notes = str(data.get("notes", {}).get("firmware", ""))
    assert "firmware/esp32_ws_bridge" not in notes
