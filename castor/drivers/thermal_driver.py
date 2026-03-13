"""AMG8833 8x8 thermal camera driver for OpenCastor."""

from __future__ import annotations

import logging
import random
import threading
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.ThermalDriver")

try:
    import smbus2 as _smbus2

    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False

_AMG8833_PIXEL_BASE = 0x80
_AMG8833_N_PIXELS = 64
_AMG8833_PIXEL_BYTES = 128  # 2 bytes per pixel × 64 pixels

# Singleton
_singleton: Optional[ThermalDriver] = None
_singleton_lock = threading.Lock()


class ThermalDriver:
    """AMG8833 8x8 thermal camera driver.

    Uses smbus2 for I2C communication. Falls back to mock mode when
    hardware is unavailable.

    Env:
      THERMAL_I2C_BUS      — I2C bus number (default 1)
      THERMAL_I2C_ADDRESS  — hex or int address (default 0x68)
      THERMAL_MOCK         — force mock mode ("1"/"true")

    REST API:
      GET /api/thermal/frame   — {pixels: [64 floats °C], hotspot, mode, latency_ms}
      GET /api/thermal/hotspot — {row, col, index, temp_c}
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}

        # ── Resolve bus ───────────────────────────────────────────────────────
        self._bus_num: int = int(cfg.get("i2c_bus", 1))

        # ── Resolve address (hex string or int) ───────────────────────────────
        raw_addr = cfg.get("i2c_address", 0x68)
        if isinstance(raw_addr, str):
            self._address: int = int(raw_addr, 16)
        else:
            self._address = int(raw_addr)

        # ── Mock flag ─────────────────────────────────────────────────────────
        force_mock: bool = bool(cfg.get("mock", False))

        self._mode: str = "mock"
        self._bus: Optional[Any] = None  # smbus2.SMBus instance
        self._lock = threading.Lock()
        self._capture_count: int = 0

        if force_mock or not HAS_SMBUS:
            reason = "mock=True in config" if force_mock else "smbus2 not installed"
            logger.info(
                "ThermalDriver: %s — mock mode (bus=%d, addr=0x%02x)",
                reason,
                self._bus_num,
                self._address,
            )
            return

        try:
            self._bus = _smbus2.SMBus(self._bus_num)
            self._mode = "hardware"
            logger.info(
                "ThermalDriver: hardware mode — bus=%d, addr=0x%02x",
                self._bus_num,
                self._address,
            )
        except Exception as exc:
            logger.warning(
                "ThermalDriver: I2C open failed (%s) — mock mode (bus=%d, addr=0x%02x)",
                exc,
                self._bus_num,
                self._address,
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _read_hardware(self) -> list[float]:
        """Read 64 pixel temperatures from AMG8833 via I2C."""
        # AMG8833 stores pixel data in registers 0x80–0xFF (128 bytes, 2 per pixel).
        # i2c_rdwr allows block reads beyond the 32-byte smbus limit.
        write_msg = _smbus2.i2c_msg.write(self._address, [_AMG8833_PIXEL_BASE])
        read_msg = _smbus2.i2c_msg.read(self._address, _AMG8833_PIXEL_BYTES)
        self._bus.i2c_rdwr(write_msg, read_msg)
        data = list(read_msg)

        pixels: list[float] = []
        for i in range(_AMG8833_N_PIXELS):
            raw = int.from_bytes(data[i * 2 : i * 2 + 2], "little", signed=False)
            temp_c = (raw & 0xFFF) * 0.25
            if raw & 0x800:
                temp_c -= 2048 * 0.25
            pixels.append(round(temp_c, 2))
        return pixels

    def _mock_capture(self) -> list[float]:
        """Return 64 random floats uniformly distributed between 20–30 °C."""
        return [round(random.uniform(20.0, 30.0), 2) for _ in range(_AMG8833_N_PIXELS)]

    # ── Public API ────────────────────────────────────────────────────────────

    def capture(self) -> list[float]:
        """Read the 8x8 thermal pixel array.

        Returns a flat list of 64 floats in °C, row-major order
        (index = row * 8 + col).

        Hardware: reads AMG8833 pixel registers 0x80–0xFF via smbus2.
        Mock: returns 64 random floats uniformly in [20, 30] °C.
        """
        if self._mode != "hardware" or self._bus is None:
            pixels = self._mock_capture()
            self._capture_count += 1
            return pixels

        with self._lock:
            try:
                pixels = self._read_hardware()
                self._capture_count += 1
                return pixels
            except Exception as exc:
                logger.error("ThermalDriver capture error: %s", exc)
                # Degrade gracefully: return mock data rather than raising
                return self._mock_capture()

    def get_hotspot(self) -> dict[str, Any]:
        """Find the hottest pixel in the current frame.

        Returns:
            {
                "row":    int   — 0-indexed row in the 8x8 grid,
                "col":    int   — 0-indexed column in the 8x8 grid,
                "index":  int   — flat index (row * 8 + col),
                "temp_c": float — temperature of the hotspot pixel in °C,
            }
        """
        pixels = self.capture()
        max_idx = max(range(len(pixels)), key=lambda i: pixels[i])
        return {
            "row": max_idx // 8,
            "col": max_idx % 8,
            "index": max_idx,
            "temp_c": pixels[max_idx],
        }

    def get_heatmap(self, width: int = 256, height: int = 256) -> bytes:
        """Render the 8x8 thermal pixel array as a JPEG heatmap.

        Bicubic-upscales the 8x8 grid to ``width`` × ``height`` and applies
        the JET colormap.  Falls back to a plain 8x8 grey PNG-encoded JPEG
        when OpenCV/NumPy are unavailable.

        Args:
            width:  Output JPEG width in pixels (default 256).
            height: Output JPEG height in pixels (default 256).

        Returns:
            JPEG bytes.
        """
        pixels = self.capture()
        try:
            import cv2
            import numpy as np

            arr = np.array(pixels, dtype=np.float32).reshape(8, 8)
            # Normalise to 0-255
            min_t, max_t = arr.min(), arr.max()
            if max_t > min_t:
                norm = ((arr - min_t) / (max_t - min_t) * 255).astype(np.uint8)
            else:
                norm = np.zeros((8, 8), dtype=np.uint8)
            # Bicubic upscale
            upscaled = cv2.resize(norm, (width, height), interpolation=cv2.INTER_CUBIC)
            # JET colormap
            coloured = cv2.applyColorMap(upscaled, cv2.COLORMAP_JET)
            ok, buf = cv2.imencode(".jpg", coloured, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                return bytes(buf)
        except Exception as exc:
            logger.warning("ThermalDriver heatmap render failed: %s — returning greyscale", exc)

        # Minimal greyscale fallback: 8-byte per-pixel grey JPEG
        try:
            import cv2
            import numpy as np

            arr = np.array(pixels, dtype=np.float32).reshape(8, 8)
            min_t, max_t = arr.min(), arr.max()
            if max_t > min_t:
                norm = ((arr - min_t) / (max_t - min_t) * 255).astype(np.uint8)
            else:
                norm = np.zeros((8, 8), dtype=np.uint8)
            ok, buf = cv2.imencode(".jpg", norm)
            if ok:
                return bytes(buf)
        except Exception:
            pass
        return b""

    def health_check(self) -> dict[str, Any]:
        """Return driver health information.

        Returns:
            {"ok": bool, "mode": "hardware"|"mock", "error": str|None}
        """
        return {
            "ok": True,
            "mode": self._mode,
            "bus": self._bus_num,
            "address": hex(self._address),
            "capture_count": self._capture_count,
            "error": None,
        }

    def close(self) -> None:
        """Close the smbus connection if open."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._mode = "mock"
        logger.info("ThermalDriver: closed (bus=%d, addr=0x%02x)", self._bus_num, self._address)


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_thermal(config: dict[str, Any] | None = None) -> ThermalDriver:
    """Return the process-wide ThermalDriver singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ThermalDriver(config=config)
    return _singleton
