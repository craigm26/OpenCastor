"""INA219 I2C power-monitor / battery driver for OpenCastor.

Reads bus voltage, shunt current, and computed power from an INA219 sensor.
Falls back to mock mode when smbus2 is unavailable or the chip cannot be
reached on the I2C bus.

INA219 register map (used here):
  0x00 — Configuration  (write 0x399F: 32 V range, 320 mV shunt, continuous)
  0x01 — Shunt Voltage  (signed 16-bit, LSB = 10 µV)
  0x02 — Bus Voltage    (bits 15:3 = raw, LSB = 4 mV, bit 1 = CNVR, bit 0 = OVF)
  0x03 — Power          (unsigned 16-bit, LSB = 20 mW)
  0x04 — Current        (signed 16-bit, LSB = 1 mA with 0x1000 calibration)
  0x05 — Calibration    (write 0x1000: 1 mA/LSB at 0.1 Ω shunt)

Env:
  BATTERY_I2C_BUS       — I2C bus number (default 1)
  BATTERY_I2C_ADDRESS   — hex string or int address (default 0x40)
  BATTERY_MOCK          — force mock mode ("1" or "true")
  BATTERY_CELL_TYPE     — "lipo" (default) or "lipo3s"
  BATTERY_HISTORY_DB    — SQLite path for charge/discharge history
                          (default ~/.castor/battery_history.db; set to ""
                          or "none" to disable)
  BATTERY_HISTORY_WINDOW_S — retention window in seconds (default 86400.0)

REST API (wired in castor/api.py):
  GET /api/battery/read   — {voltage_v, current_ma, power_mw, percent, mode}
  GET /api/battery/health — {ok, mode, bus, address, read_count, error}

Install: pip install smbus2
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.BatteryDriver")

try:
    import smbus2 as _smbus2

    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False

# ── INA219 register addresses ─────────────────────────────────────────────────
_REG_CONFIG = 0x00
_REG_SHUNT_VOLTAGE = 0x01
_REG_BUS_VOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Configuration value: 32 V range, 320 mV shunt range, continuous shunt+bus
_CONFIG_VALUE = 0x399F

# Calibration value: 1 mA/LSB with 0.1 Ω shunt resistor
_CALIB_VALUE = 0x1000

# LSB scales
_BUS_VOLTAGE_LSB_V = 0.004  # 4 mV per LSB
_SHUNT_VOLTAGE_LSB_UV = 10  # 10 µV per LSB
_CURRENT_LSB_MA = 1.0  # 1 mA per LSB (with _CALIB_VALUE)
_POWER_LSB_MW = 20.0  # 20 mW per LSB

# ── History constants ──────────────────────────────────────────────────────────
_HISTORY_PRUNE_INTERVAL = 1000  # prune every N inserts
_HISTORY_DEFAULT_WINDOW_S = 86400.0  # 24 hours

_HISTORY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    voltage_v  REAL,
    current_ma REAL,
    power_mw   REAL,
    percent    REAL,
    mode       TEXT
);
"""
_HISTORY_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_ts ON readings (ts DESC);"

# ── Singleton ─────────────────────────────────────────────────────────────────
_singleton: Optional[BatteryDriver] = None
_singleton_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _estimate_percent(voltage_v: float, cell_type: str) -> float:
    """Estimate battery percentage from bus voltage.

    Args:
        voltage_v:  Measured bus voltage in volts.
        cell_type:  "lipo3s" → 10.0–13.0 V range;
                    anything else → single-cell LiPo 3.0–4.2 V range.

    Returns:
        Float in [0.0, 100.0].
    """
    if cell_type == "lipo3s":
        return max(0.0, min(100.0, (voltage_v - 10.0) / (13.0 - 10.0) * 100.0))
    # Default: single-cell LiPo
    return max(0.0, min(100.0, (voltage_v - 3.0) / (4.2 - 3.0) * 100.0))


def _resolve_history_db_path() -> Optional[str]:
    """Resolve the history DB path from env or default.

    Returns None when logging is disabled (empty string or "none").
    """
    raw = os.getenv("BATTERY_HISTORY_DB", "").strip()
    if raw == "":
        # Use default path
        raw = os.path.join(os.path.expanduser("~"), ".castor", "battery_history.db")
    if raw.lower() == "none":
        return None
    return raw


class BatteryDriver:
    """INA219 I2C power-monitor driver.

    Uses smbus2 for I2C communication. Falls back to mock mode when
    hardware is unavailable or when explicitly requested.

    Env:
      BATTERY_I2C_BUS       — I2C bus number (default 1)
      BATTERY_I2C_ADDRESS   — hex or int address (default 0x40)
      BATTERY_MOCK          — force mock mode ("1"/"true")
      BATTERY_CELL_TYPE     — "lipo" (default) or "lipo3s"
      BATTERY_HISTORY_DB    — SQLite path for history; "" = default; "none" = disabled
      BATTERY_HISTORY_WINDOW_S — history retention window in seconds (default 86400.0)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}

        # ── Resolve bus ───────────────────────────────────────────────────────
        self._bus_num: int = int(cfg.get("i2c_bus", os.getenv("BATTERY_I2C_BUS", "1")))

        # ── Resolve address (hex string or int) ───────────────────────────────
        raw_addr = cfg.get("i2c_address", os.getenv("BATTERY_I2C_ADDRESS", "0x40"))
        if isinstance(raw_addr, str):
            self._address: int = int(raw_addr, 16)
        else:
            self._address = int(raw_addr)

        # ── Cell type ─────────────────────────────────────────────────────────
        self._cell_type: str = cfg.get("cell_type", os.getenv("BATTERY_CELL_TYPE", "lipo"))

        # ── Mock flag ─────────────────────────────────────────────────────────
        env_mock = os.getenv("BATTERY_MOCK", "").strip().lower() in ("1", "true")
        force_mock: bool = bool(cfg.get("mock", False)) or env_mock

        self._mode: str = "mock"
        self._bus: Optional[Any] = None  # smbus2.SMBus instance
        self._lock = threading.Lock()
        self._read_count: int = 0
        self._last_error: Optional[str] = None

        # ── History DB ────────────────────────────────────────────────────────
        self._history_db_path: Optional[str] = _resolve_history_db_path()
        self._history_con: Optional[Any] = None  # sqlite3.Connection
        self._history_insert_count: int = 0
        self._history_window_s: float = float(
            os.getenv("BATTERY_HISTORY_WINDOW_S", str(_HISTORY_DEFAULT_WINDOW_S))
        )

        if force_mock or not HAS_SMBUS:
            reason = "mock=True in config" if force_mock else "smbus2 not installed"
            logger.info(
                "BatteryDriver: %s — mock mode (bus=%d, addr=0x%02x)",
                reason,
                self._bus_num,
                self._address,
            )
            return

        try:
            bus = _smbus2.SMBus(self._bus_num)
            # Write calibration register first, then configuration
            self._write_word(bus, _REG_CALIBRATION, _CALIB_VALUE)
            self._write_word(bus, _REG_CONFIG, _CONFIG_VALUE)
            self._bus = bus
            self._mode = "hardware"
            logger.info(
                "BatteryDriver: hardware mode — bus=%d, addr=0x%02x, cell_type=%s",
                self._bus_num,
                self._address,
                self._cell_type,
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "BatteryDriver: I2C open failed (%s) — mock mode (bus=%d, addr=0x%02x)",
                exc,
                self._bus_num,
                self._address,
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _write_word(self, bus: Any, register: int, value: int) -> None:
        """Write a 16-bit word to *register* in big-endian byte order."""
        high = (value >> 8) & 0xFF
        low = value & 0xFF
        bus.write_i2c_block_data(self._address, register, [high, low])

    def _read_word(self, register: int) -> int:
        """Read a 16-bit word from *register* (big-endian, unsigned)."""
        raw = self._bus.read_word_data(self._address, register)
        # smbus2 returns little-endian on x86; swap bytes to big-endian
        return int.from_bytes(raw.to_bytes(2, "little"), "big")

    def _read_hardware(self) -> dict[str, Any]:
        """Read voltage, current, and power from INA219 registers."""
        # Bus voltage: bits 15:3 are the raw value; shift right by 3 then × 4 mV
        bus_raw = self._read_word(_REG_BUS_VOLTAGE)
        voltage_v = round(((bus_raw >> 3) & 0x1FFF) * _BUS_VOLTAGE_LSB_V, 4)

        # Current: signed 16-bit, LSB = 1 mA
        current_raw = self._read_word(_REG_CURRENT)
        # Interpret as signed
        current_raw_signed = int.from_bytes(current_raw.to_bytes(2, "big"), "big", signed=False)
        if current_raw_signed >= 0x8000:
            current_raw_signed -= 0x10000
        current_ma = round(current_raw_signed * _CURRENT_LSB_MA, 2)

        # Power: unsigned 16-bit, LSB = 20 mW
        power_raw = self._read_word(_REG_POWER)
        power_mw = round(power_raw * _POWER_LSB_MW, 2)

        return {
            "voltage_v": voltage_v,
            "current_ma": current_ma,
            "power_mw": power_mw,
        }

    def _mock_read(self) -> dict[str, Any]:
        """Return plausible randomised values near 11.8 V / 500 mA."""
        voltage_v = round(random.uniform(11.5, 12.5), 3)
        current_ma = round(random.uniform(400.0, 600.0), 2)
        power_mw = round(voltage_v * current_ma, 2)
        return {
            "voltage_v": voltage_v,
            "current_ma": current_ma,
            "power_mw": power_mw,
        }

    def _ensure_history_db(self) -> bool:
        """Open and initialise the history SQLite DB if not already done.

        Returns True when the connection is ready, False on any error.
        """
        if self._history_con is not None:
            return True
        if self._history_db_path is None:
            return False
        try:
            db_dir = os.path.dirname(self._history_db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            con = sqlite3.connect(self._history_db_path, check_same_thread=False)
            con.execute(_HISTORY_CREATE_TABLE)
            con.execute(_HISTORY_CREATE_INDEX)
            con.commit()
            self._history_con = con
            logger.debug("BatteryDriver: history DB opened at %s", self._history_db_path)
            return True
        except Exception as exc:
            logger.warning("BatteryDriver: could not open history DB: %s", exc)
            return False

    def _log_history(self, data: dict[str, Any]) -> None:
        """Append one reading to the history DB.

        Silently swallows all exceptions so that a DB failure never
        propagates into ``read()``.
        """
        if self._history_db_path is None:
            return
        try:
            if not self._ensure_history_db():
                return
            ts = time.time()
            self._history_con.execute(  # type: ignore[union-attr]
                "INSERT INTO readings (ts, voltage_v, current_ma, power_mw, percent, mode) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    data.get("voltage_v"),
                    data.get("current_ma"),
                    data.get("power_mw"),
                    data.get("percent"),
                    data.get("mode"),
                ),
            )
            self._history_con.commit()  # type: ignore[union-attr]
            self._history_insert_count += 1

            # Auto-prune every _HISTORY_PRUNE_INTERVAL inserts
            if self._history_insert_count % _HISTORY_PRUNE_INTERVAL == 0:
                cutoff = ts - self._history_window_s * 2
                self._history_con.execute(  # type: ignore[union-attr]
                    "DELETE FROM readings WHERE ts < ?", (cutoff,)
                )
                self._history_con.commit()  # type: ignore[union-attr]
                logger.debug(
                    "BatteryDriver: pruned history rows older than %.0f s",
                    self._history_window_s * 2,
                )
        except Exception as exc:
            logger.warning("BatteryDriver: history log error: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def read(self) -> dict[str, Any]:
        """Read battery state from INA219 (or mock).

        Returns:
            {
                "voltage_v":  float — bus voltage in volts,
                "current_ma": float — shunt current in milliamps,
                "power_mw":   float — computed power in milliwatts,
                "percent":    float — estimated charge percentage [0–100],
                "mode":       str   — "hardware" or "mock",
            }
        """
        if self._mode != "hardware" or self._bus is None:
            data = self._mock_read()
            self._read_count += 1
            data["percent"] = round(_estimate_percent(data["voltage_v"], self._cell_type), 1)
            data["mode"] = self._mode
            self._log_history(data)
            return data

        with self._lock:
            try:
                data = self._read_hardware()
                self._read_count += 1
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                logger.error("BatteryDriver read error: %s — returning mock data", exc)
                data = self._mock_read()
                self._read_count += 1

        data["percent"] = round(_estimate_percent(data["voltage_v"], self._cell_type), 1)
        data["mode"] = self._mode
        self._log_history(data)
        return data

    def get_history(self, window_s: float = 86400.0, limit: int = 1000) -> list[dict[str, Any]]:
        """Return recent battery readings from the history DB.

        Args:
            window_s: Time window in seconds to look back (default 24 h).
            limit:    Maximum number of rows to return (default 1000).

        Returns:
            List of dicts with keys ``{ts, voltage_v, current_ma, power_mw, percent, mode}``,
            ordered newest-first. Returns an empty list when history is disabled or on error.
        """
        if self._history_db_path is None:
            return []
        try:
            if not self._ensure_history_db():
                return []
            cutoff = time.time() - window_s
            cur = self._history_con.execute(  # type: ignore[union-attr]
                "SELECT ts, voltage_v, current_ma, power_mw, percent, mode "
                "FROM readings WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (cutoff, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "ts": row[0],
                    "voltage_v": row[1],
                    "current_ma": row[2],
                    "power_mw": row[3],
                    "percent": row[4],
                    "mode": row[5],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("BatteryDriver: get_history error: %s", exc)
            return []

    def get_charge_cycles(self, window_s: float = 86400.0) -> list[dict[str, Any]]:
        """Detect charge/discharge/idle cycles from history.

        Scans the stored readings within *window_s* seconds (oldest-first) and
        groups consecutive readings with the same current-sign category into
        cycle segments.  Sign categories:

        * ``"charge"``    — current_ma > +50 mA
        * ``"discharge"`` — current_ma < -50 mA
        * ``"idle"``      — |current_ma| ≤ 50 mA

        A new cycle is started whenever the sign category changes.

        Args:
            window_s: How far back to look, in seconds (default 24 h).

        Returns:
            List of cycle dicts ordered oldest-first::

                {
                    "start_ts":      float,
                    "end_ts":        float,
                    "type":          "charge" | "discharge" | "idle",
                    "delta_percent": float,   # percent[end] - percent[start]
                    "duration_s":    float,   # end_ts - start_ts
                }

            Returns an empty list when history is disabled, the DB is empty,
            or fewer than two readings are available.  Never raises.
        """
        if self._history_db_path is None:
            return []
        try:
            if not self._ensure_history_db():
                return []
            cutoff = time.time() - window_s
            cur = self._history_con.execute(  # type: ignore[union-attr]
                "SELECT ts, current_ma, percent FROM readings WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,),
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                return []

            def _sign_category(current_ma: float) -> str:
                if current_ma is None:
                    return "idle"
                if current_ma > 50.0:
                    return "charge"
                if current_ma < -50.0:
                    return "discharge"
                return "idle"

            cycles: list[dict[str, Any]] = []
            seg_start_ts: float = rows[0][0]
            seg_start_pct: float = rows[0][2] if rows[0][2] is not None else 0.0
            seg_type: str = _sign_category(rows[0][1])
            seg_end_ts: float = rows[0][0]
            seg_end_pct: float = seg_start_pct

            for ts, current_ma, percent in rows[1:]:
                cat = _sign_category(current_ma)
                pct = percent if percent is not None else seg_end_pct
                if cat != seg_type:
                    # Flush completed segment
                    cycles.append(
                        {
                            "start_ts": seg_start_ts,
                            "end_ts": seg_end_ts,
                            "type": seg_type,
                            "delta_percent": round(seg_end_pct - seg_start_pct, 2),
                            "duration_s": round(seg_end_ts - seg_start_ts, 3),
                        }
                    )
                    seg_start_ts = ts
                    seg_start_pct = pct
                    seg_type = cat
                seg_end_ts = ts
                seg_end_pct = pct

            # Flush the final segment
            cycles.append(
                {
                    "start_ts": seg_start_ts,
                    "end_ts": seg_end_ts,
                    "type": seg_type,
                    "delta_percent": round(seg_end_pct - seg_start_pct, 2),
                    "duration_s": round(seg_end_ts - seg_start_ts, 3),
                }
            )
            return cycles
        except Exception as exc:
            logger.warning("BatteryDriver: get_charge_cycles error: %s", exc)
            return []

    def health_check(self) -> dict[str, Any]:
        """Return driver health information.

        Returns:
            {
                "ok":         bool,
                "mode":       "hardware" | "mock",
                "bus":        int,
                "address":    str  — e.g. "0x40",
                "read_count": int,
                "error":      str | None,
            }
        """
        return {
            "ok": True,
            "mode": self._mode,
            "bus": self._bus_num,
            "address": hex(self._address),
            "read_count": self._read_count,
            "error": self._last_error,
        }

    def close(self) -> None:
        """Close the smbus connection and history DB if open."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._mode = "mock"
        if self._history_con is not None:
            try:
                self._history_con.close()
            except Exception:
                pass
            self._history_con = None
        logger.info(
            "BatteryDriver: closed (bus=%d, addr=0x%02x)",
            self._bus_num,
            self._address,
        )


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_battery(config: dict[str, Any] | None = None) -> BatteryDriver:
    """Return the process-wide BatteryDriver singleton.

    Thread-safe: the first call instantiates the driver; subsequent calls
    return the cached instance regardless of *config*.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BatteryDriver(config=config)
    return _singleton
