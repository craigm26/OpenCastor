"""Tests for safety state telemetry snapshots."""

import time

import pytest

from castor.safety.state import SafetyStateSnapshot, SafetyTelemetry, compute_safety_score


class TestSafetyStateSnapshot:
    def test_defaults(self):
        snap = SafetyStateSnapshot()
        assert snap.estop_active is False
        assert snap.safety_score == 1.0
        assert snap.locked_out_principals == []
        assert snap.active_violations == {}

    def test_to_dict(self):
        snap = SafetyStateSnapshot(timestamp=1000.0, estop_active=True, safety_score=0.5)
        d = snap.to_dict()
        assert d["timestamp"] == 1000.0
        assert d["estop_active"] is True
        assert d["safety_score"] == 0.5
        assert isinstance(d, dict)

    def test_from_dict(self):
        d = {
            "timestamp": 2000.0,
            "estop_active": False,
            "locked_out_principals": ["brain"],
            "active_violations": {"brain": 3},
            "motor_rate_usage": 0.5,
            "active_work_orders": 10,
            "anti_subversion_flags": 2,
            "uptime_seconds": 3600.0,
            "safety_score": 0.8,
        }
        snap = SafetyStateSnapshot.from_dict(d)
        assert snap.timestamp == 2000.0
        assert snap.locked_out_principals == ["brain"]
        assert snap.active_violations == {"brain": 3}

    def test_from_dict_ignores_unknown(self):
        d = {"timestamp": 1.0, "unknown_field": "ignored"}
        snap = SafetyStateSnapshot.from_dict(d)
        assert snap.timestamp == 1.0

    def test_roundtrip(self):
        original = SafetyStateSnapshot(
            timestamp=time.time(),
            estop_active=True,
            locked_out_principals=["agent1", "agent2"],
            active_violations={"agent1": 5},
            motor_rate_usage=0.75,
            safety_score=0.3,
        )
        restored = SafetyStateSnapshot.from_dict(original.to_dict())
        assert restored == original


class TestComputeSafetyScore:
    def test_perfect_health(self):
        snap = SafetyStateSnapshot()
        assert compute_safety_score(snap) == 1.0

    def test_estop_penalty(self):
        snap = SafetyStateSnapshot(estop_active=True)
        assert compute_safety_score(snap) == 0.5

    def test_lockout_penalty(self):
        snap = SafetyStateSnapshot(locked_out_principals=["a", "b"])
        assert compute_safety_score(snap) == 0.8

    def test_lockout_penalty_capped(self):
        snap = SafetyStateSnapshot(locked_out_principals=["a", "b", "c", "d", "e"])
        # Max lockout penalty is 0.3
        assert compute_safety_score(snap) == 0.7

    def test_motor_rate_high(self):
        snap = SafetyStateSnapshot(motor_rate_usage=0.9)
        assert compute_safety_score(snap) == 0.9

    def test_motor_rate_below_threshold(self):
        snap = SafetyStateSnapshot(motor_rate_usage=0.5)
        assert compute_safety_score(snap) == 1.0

    def test_violations_penalty(self):
        snap = SafetyStateSnapshot(active_violations={"a": 1, "b": 2, "c": 3})
        assert compute_safety_score(snap) == 0.85

    def test_violations_penalty_capped(self):
        snap = SafetyStateSnapshot(active_violations={f"p{i}": 1 for i in range(10)})
        # Max violation penalty is 0.2
        assert compute_safety_score(snap) == 0.8

    def test_anti_subversion_penalty(self):
        snap = SafetyStateSnapshot(anti_subversion_flags=3)
        assert compute_safety_score(snap) == 0.9

    def test_all_penalties_combined(self):
        snap = SafetyStateSnapshot(
            estop_active=True,
            locked_out_principals=["a", "b", "c", "d"],
            motor_rate_usage=0.95,
            active_violations={"x": 1, "y": 2, "z": 3, "w": 4, "v": 5},
            anti_subversion_flags=1,
        )
        score = compute_safety_score(snap)
        # 1.0 - 0.5 (estop) - 0.3 (lockout cap) - 0.1 (motor) - 0.2 (violations cap) - 0.1 (subversion) = -0.2 → 0.0
        assert score == 0.0

    def test_score_never_negative(self):
        snap = SafetyStateSnapshot(
            estop_active=True,
            locked_out_principals=["a"] * 10,
            active_violations={f"p{i}": 99 for i in range(20)},
            motor_rate_usage=1.0,
            anti_subversion_flags=100,
        )
        assert compute_safety_score(snap) >= 0.0

    def test_score_never_above_one(self):
        snap = SafetyStateSnapshot()
        assert compute_safety_score(snap) <= 1.0


class TestSafetyTelemetry:
    def _make_mock_safety_layer(self, estop=False, lockouts=None, violations=None, motor_ts=None):
        """Create a minimal mock SafetyLayer for testing."""

        class MockNamespace:
            def __init__(self):
                self._data = {}

            def read(self, path):
                return self._data.get(path, [])

            def write(self, path, data, **kw):
                self._data[path] = data
                return True

            def mkdir(self, path, **kw):
                return True

            def append(self, path, entry):
                self._data.setdefault(path, []).append(entry)

        class MockPerms:
            def check_access(self, *a, **kw):
                return True

            def get_caps(self, *a):
                return 0

            def dump(self):
                return {}

        ns = MockNamespace()
        ns._data["/var/log/safety"] = []
        ns._data["/var/log/actions"] = []

        class MockSafetyLayer:
            pass

        sl = MockSafetyLayer()
        sl.ns = ns
        sl._estop = estop
        sl._lockouts = lockouts or {}
        sl._violations = violations or {}
        sl._motor_timestamps = motor_ts or []
        sl.limits = {"motor_rate_hz": 20.0}
        return sl

    def test_snapshot_healthy(self):
        sl = self._make_mock_safety_layer()
        telem = SafetyTelemetry(start_time=time.time() - 100)
        snap = telem.snapshot(sl)
        assert snap.estop_active is False
        assert snap.safety_score == 1.0
        assert snap.uptime_seconds >= 99

    def test_snapshot_estop(self):
        sl = self._make_mock_safety_layer(estop=True)
        telem = SafetyTelemetry()
        snap = telem.snapshot(sl)
        assert snap.estop_active is True
        assert snap.safety_score == 0.5

    def test_snapshot_with_lockouts(self):
        future = time.time() + 1000
        sl = self._make_mock_safety_layer(lockouts={"agent1": future, "agent2": future})
        telem = SafetyTelemetry()
        snap = telem.snapshot(sl)
        assert len(snap.locked_out_principals) == 2

    def test_snapshot_expired_lockout_excluded(self):
        past = time.time() - 100
        sl = self._make_mock_safety_layer(lockouts={"agent1": past})
        telem = SafetyTelemetry()
        snap = telem.snapshot(sl)
        assert len(snap.locked_out_principals) == 0

    def test_snapshot_motor_rate(self):
        now = time.time()
        ts = [now - 0.1 * i for i in range(10)]  # 10 in last second
        sl = self._make_mock_safety_layer(motor_ts=ts)
        telem = SafetyTelemetry()
        snap = telem.snapshot(sl)
        assert snap.motor_rate_usage == pytest.approx(0.5, abs=0.05)

    def test_snapshot_dict(self):
        sl = self._make_mock_safety_layer()
        telem = SafetyTelemetry()
        d = telem.snapshot_dict(sl)
        assert isinstance(d, dict)
        assert "safety_score" in d
        assert "timestamp" in d
