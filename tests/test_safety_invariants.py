"""Tests for RCAN Safety Invariants 4 & 5 and E-Stop auth code (R9).

Verifies that rate limiting and session timeout are enforced in
read(), write(), append(), ls(), stat(), and mkdir().
"""

import os
import time
from unittest.mock import patch

from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import SafetyLayer


class _Base:
    """Shared helpers for safety invariant tests."""

    def _make_safety(self, **limit_overrides):
        ns = Namespace()
        perms = PermissionTable()
        limits = {"motor_rate_hz": 100.0, **limit_overrides}
        return SafetyLayer(ns, perms, limits=limits), ns, perms


# =====================================================================
# Rate limiting (RCAN Safety Invariant 4)
# =====================================================================
class TestRoleRateLimiting(_Base):
    """check_role_rate_limit blocks requests when the role limit is exceeded."""

    def _exhaust_rate_limit(self, sl, principal, n):
        """Issue n reads to exhaust the rate limit."""
        for _ in range(n):
            sl.read("/proc/uptime", principal=principal)

    def test_rate_limit_blocks_read(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 42)
        # GUEST has 10 req/min
        for _i in range(10):
            assert sl.read("/proc/uptime", principal="driver") == 42
        # 11th should be blocked
        assert sl.read("/proc/uptime", principal="driver") is None

    def test_rate_limit_blocks_write(self):
        sl, ns, _ = self._make_safety()
        # driver (GUEST) has 10 req/min; use /tmp which is writable
        for _i in range(10):
            sl.read("/proc/uptime", principal="driver")
        assert sl.write("/tmp/x", "data", principal="driver") is False

    def test_rate_limit_blocks_ls(self):
        sl, ns, _ = self._make_safety()
        for _ in range(10):
            sl.read("/proc/uptime", principal="driver")
        assert sl.ls("/tmp", principal="driver") is None

    def test_rate_limit_blocks_stat(self):
        sl, ns, _ = self._make_safety()
        ns.write("/tmp/f", "x")
        for _ in range(10):
            sl.read("/proc/uptime", principal="driver")
        assert sl.stat("/tmp/f", principal="driver") is None

    def test_rate_limit_blocks_append(self):
        sl, ns, _ = self._make_safety()
        ns.write("/tmp/log", [])
        for _ in range(10):
            sl.read("/proc/uptime", principal="driver")
        assert sl.append("/tmp/log", "entry", principal="driver") is False

    def test_rate_limit_blocks_mkdir(self):
        sl, ns, _ = self._make_safety()
        for _ in range(10):
            sl.read("/proc/uptime", principal="driver")
        assert sl.mkdir("/tmp/newdir", principal="driver") is False

    def test_rate_limit_does_not_block_creator(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 1)
        # root is CREATOR with unlimited rate
        for _ in range(200):
            assert sl.read("/proc/uptime", principal="root") == 1

    def test_rate_limit_audits_denial(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 1)
        for _ in range(10):
            sl.read("/proc/uptime", principal="driver")
        sl.read("/proc/uptime", principal="driver")
        safety_log = ns.read("/var/log/safety")
        rate_events = [e for e in safety_log if e.get("event") == "role_rate_limited"]
        assert len(rate_events) >= 1


# =====================================================================
# Session timeout (RCAN Safety Invariant 5)
# =====================================================================
class TestSessionTimeout(_Base):
    """check_session_timeout blocks expired sessions."""

    def test_session_timeout_blocks_read(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 42)
        # First read starts the session for driver (GUEST, 300s timeout)
        assert sl.read("/proc/uptime", principal="driver") == 42
        # Fast-forward past timeout
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.read("/proc/uptime", principal="driver") is None

    def test_session_timeout_blocks_write(self):
        sl, ns, _ = self._make_safety()
        sl.read("/proc/uptime", principal="driver")  # start session
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.write("/tmp/x", "data", principal="driver") is False

    def test_session_timeout_blocks_ls(self):
        sl, ns, _ = self._make_safety()
        sl.read("/proc/uptime", principal="driver")
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.ls("/tmp", principal="driver") is None

    def test_session_timeout_blocks_stat(self):
        sl, ns, _ = self._make_safety()
        ns.write("/tmp/f", "x")
        sl.read("/proc/uptime", principal="driver")
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.stat("/tmp/f", principal="driver") is None

    def test_session_timeout_blocks_append(self):
        sl, ns, _ = self._make_safety()
        ns.write("/tmp/log", [])
        sl.read("/proc/uptime", principal="driver")
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.append("/tmp/log", "entry", principal="driver") is False

    def test_session_timeout_blocks_mkdir(self):
        sl, ns, _ = self._make_safety()
        sl.read("/proc/uptime", principal="driver")
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        assert sl.mkdir("/tmp/newdir", principal="driver") is False

    def test_session_timeout_does_not_block_creator(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 1)
        # root (CREATOR) has no timeout
        sl.read("/proc/uptime", principal="root")
        with sl._lock:
            sl._session_starts["root"] = time.time() - 999999
        assert sl.read("/proc/uptime", principal="root") == 1

    def test_reset_session_clears_timeout(self):
        sl, ns, _ = self._make_safety()
        ns.write("/proc/uptime", 42)
        sl.read("/proc/uptime", principal="driver")
        with sl._lock:
            sl._session_starts["driver"] = time.time() - 400
        # Should be timed out
        assert sl.read("/proc/uptime", principal="driver") is None
        # Reset and clear rate limit timestamps too
        sl.reset_session("driver")
        with sl._lock:
            sl._role_request_timestamps.pop("driver", None)
        assert sl.read("/proc/uptime", principal="driver") == 42


# =====================================================================
# E-Stop authorization code (R9)
# =====================================================================
class TestEstopAuthCode(_Base):
    """clear_estop requires auth code when OPENCASTOR_ESTOP_AUTH is set."""

    def test_clear_estop_no_env_works(self):
        sl, _, _ = self._make_safety()
        sl.estop(principal="root")
        assert sl.is_estopped
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCASTOR_ESTOP_AUTH", None)
            assert sl.clear_estop(principal="root")
        assert not sl.is_estopped

    def test_clear_estop_with_env_requires_code(self):
        sl, _, _ = self._make_safety()
        sl.estop(principal="root")
        with patch.dict(os.environ, {"OPENCASTOR_ESTOP_AUTH": "secret123"}):
            # No code → denied
            assert not sl.clear_estop(principal="root")
            assert sl.is_estopped
            # Wrong code → denied
            assert not sl.clear_estop(principal="root", auth_code="wrong")
            assert sl.is_estopped
            # Correct code → allowed
            assert sl.clear_estop(principal="root", auth_code="secret123")
            assert not sl.is_estopped

    def test_clear_estop_auth_audits_denial(self):
        sl, ns, _ = self._make_safety()
        sl.estop(principal="root")
        with patch.dict(os.environ, {"OPENCASTOR_ESTOP_AUTH": "code42"}):
            sl.clear_estop(principal="root", auth_code="bad")
        safety_log = ns.read("/var/log/safety")
        deny_events = [e for e in safety_log if e.get("event") == "deny_clear_estop"]
        assert any("auth code" in e.get("detail", "") for e in deny_events)
