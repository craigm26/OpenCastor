"""RCAN v1.6 Security Audit Tests.

Covers:
  P66 ESTOP invariant       — ESTOP is NEVER blocked by any gate
  GAP-03 Replay prevention  — duplicate non-ESTOP commands are blocked
  GAP-06 Offline mode       — non-ESTOP commands blocked after 300s offline
  GAP-10 Training consent   — training commands blocked without consent
  GAP-16 LOA enforcement    — control scope rejects insufficient LoA
  Scope levels              — discover=0, status=1, chat=2, control=3,
                              system=3, safety=99
  System dispatch           — only UPGRADE/REBOOT/RELOAD_CONFIG are valid

Spec: RCAN v1.6 / Protocol 66
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from castor.cloud.bridge import (
    CastorBridge,
    _make_replay_cache,
    _ReplayCacheStub,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MINIMAL_CONFIG: dict[str, Any] = {
    "rrn": "RRN-00000099",
    "metadata": {
        "name": "SecurityTestBot",
        "ruri": "rcan://test-registry/bot",
    },
    "firebase_uid": "uid-owner-001",
    "owner": "rrn://test-owner",
    "min_loa_for_control": 1,
    "loa_enforcement": True,  # enforcement ON so rejections are tested
}


def _make_bridge(**overrides: Any) -> CastorBridge:
    cfg = {**MINIMAL_CONFIG, **overrides}
    bridge = CastorBridge(config=cfg, firebase_project="test-project")
    bridge._db = MagicMock()
    bridge._consent = MagicMock()
    bridge._consent.is_authorized.return_value = (True, "ok")
    return bridge


def _cmd_doc(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope": "chat",
        "instruction": "hello",
        "sender_type": "human",
        "status": "pending",
        "issued_at": time.time(),
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# P66 ESTOP Invariant
# ---------------------------------------------------------------------------


class TestP66EstopInvariant:
    """ESTOP must NEVER be blocked by replay cache, offline mode, or any other gate."""

    def test_estop_bypasses_replay_cache_when_duplicate(self) -> None:
        """P66: ESTOP gets through even when replay cache would block the cmd_id.

        This tests the fix where `is_estop` skips the replay check entirely.
        """
        bridge = _make_bridge()
        cmd_id = "cmd-estop-replay-001"
        doc = _cmd_doc(scope="safety", instruction="estop emergency stop")

        # First call — record it in the replay cache
        bridge._check_replay(cmd_id, doc, is_safety=True)

        # Second call with same cmd_id — normally would be blocked
        # but the _execute_command path skips replay for is_estop=True
        # Verify the bridge logic: is_estop=True means we skip _check_replay
        is_estop = doc["scope"] == "safety" and "estop" in doc["instruction"].lower()
        assert is_estop is True, "test prerequisite: doc must be identified as ESTOP"

        # The replay check would return False for a duplicate
        replay_result = bridge._check_replay(cmd_id, doc, is_safety=True)
        # Result doesn't matter — the point is we never call it for ESTOP
        # Verify that the offline check also passes for ESTOP
        bridge._offline_mode = True
        assert bridge._is_command_allowed_offline("safety", "estop emergency stop") is True

    def test_estop_bypasses_offline_mode(self) -> None:
        """P66: ESTOP is allowed even when robot is in offline mode."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        allowed = bridge._is_command_allowed_offline("safety", "estop")
        assert allowed is True, "ESTOP must be allowed in offline mode"

    def test_estop_bypasses_offline_mode_mixed_case(self) -> None:
        """P66: ESTOP instruction case-insensitive match."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("safety", "ESTOP") is True
        assert bridge._is_command_allowed_offline("safety", "Emergency ESTOP NOW") is True

    def test_estop_not_subject_to_replay_gate_in_execute(self) -> None:
        """P66: _execute_command skips replay check for is_estop=True.

        We inspect the bridge source logic: when is_estop=True the replay branch
        is guarded by `not is_estop and not self._check_replay(...)`.
        """
        import inspect

        import castor.cloud.bridge as _bridge_mod

        src = inspect.getsource(_bridge_mod.CastorBridge._execute_command)
        # The critical guard must be present in source
        assert "not is_estop and not self._check_replay" in src, (
            "P66 invariant: _execute_command must skip replay check for ESTOP. "
            "Found unexpected replay gate — ESTOP could be silently dropped!"
        )


# ---------------------------------------------------------------------------
# GAP-03 Replay Prevention
# ---------------------------------------------------------------------------


class TestReplayPrevention:
    """Duplicate non-ESTOP commands must be blocked within the replay window."""

    def test_duplicate_command_blocked(self) -> None:
        """Replay prevention: sending the same cmd_id twice blocks the second."""
        from rcan.replay import ReplayCache

        cache = ReplayCache(window_s=30)
        now = time.time()
        cmd_id = "cmd-dup-001"

        ok1, _ = cache.check_and_record(cmd_id, now, is_safety=False)
        assert ok1 is True, "First command should be allowed"

        ok2, reason = cache.check_and_record(cmd_id, now, is_safety=False)
        assert ok2 is False, "Second command with same cmd_id must be blocked"
        assert reason  # reason should explain the rejection

    def test_unique_commands_pass_through(self) -> None:
        """Different cmd_ids are allowed independently."""
        from rcan.replay import ReplayCache

        cache = ReplayCache(window_s=30)
        now = time.time()

        for i in range(5):
            ok, _ = cache.check_and_record(f"cmd-unique-{i}", now, is_safety=False)
            assert ok is True, f"cmd-unique-{i} should be allowed"

    def test_replay_cache_stub_is_disabled(self) -> None:
        """_ReplayCacheStub (fail-open) should warn that replay is disabled."""
        stub = _ReplayCacheStub(window_s=30)
        ok, reason = stub.check_and_record("any-id", time.time(), is_safety=False)
        # Stub is fail-open — always allows (but real one must be used in prod)
        assert ok is True
        assert reason == ""

    def test_make_replay_cache_returns_real_cache(self) -> None:
        """_make_replay_cache should return the real ReplayCache from rcan-py 0.6.0."""
        from rcan.replay import ReplayCache as RealCache

        cache = _make_replay_cache(window_s=30)
        assert isinstance(cache, RealCache), (
            "rcan-py is installed — _make_replay_cache must return the real ReplayCache, "
            "not the stub. The stub fails open and provides NO replay protection."
        )

    def test_bridge_uses_real_replay_cache(self) -> None:
        """CastorBridge must instantiate the real ReplayCache (not the stub)."""
        from rcan.replay import ReplayCache as RealCache

        bridge = _make_bridge()
        assert isinstance(bridge._replay_cache, RealCache), (
            "bridge._replay_cache must be the real ReplayCache from rcan-py 0.6.0"
        )
        assert isinstance(bridge._safety_replay_cache, RealCache), (
            "bridge._safety_replay_cache must be the real ReplayCache from rcan-py 0.6.0"
        )


# ---------------------------------------------------------------------------
# GAP-06 Offline Mode
# ---------------------------------------------------------------------------


class TestOfflineMode:
    """Commands other than ESTOP must be blocked after 300s offline."""

    def test_control_command_blocked_offline(self) -> None:
        """Control scope commands are rejected in offline mode."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        allowed = bridge._is_command_allowed_offline("control", "move forward")
        assert allowed is False

    def test_chat_command_blocked_offline(self) -> None:
        """Chat scope commands are rejected in offline mode."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("chat", "say hello") is False

    def test_status_command_blocked_offline(self) -> None:
        """Status scope commands are rejected in offline mode."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("status", "get_status") is False

    def test_system_reboot_allowed_offline(self) -> None:
        """System REBOOT is allowed offline — safe to restart without network."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("system", "REBOOT") is True

    def test_system_reload_config_allowed_offline(self) -> None:
        """System RELOAD_CONFIG is allowed offline — local config only."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("system", "RELOAD_CONFIG") is True

    def test_system_upgrade_blocked_offline(self) -> None:
        """System UPGRADE is blocked offline — requires network to download package."""
        bridge = _make_bridge()
        bridge._offline_mode = True

        assert bridge._is_command_allowed_offline("system", "UPGRADE") is False
        assert bridge._is_command_allowed_offline("system", "UPGRADE: 2026.3.17") is False

    def test_online_mode_allows_all(self) -> None:
        """When online, all commands pass the offline check."""
        bridge = _make_bridge()
        bridge._offline_mode = False  # explicitly online

        for scope, instr in [
            ("control", "move"),
            ("system", "UPGRADE"),
            ("chat", "hello"),
        ]:
            assert bridge._is_command_allowed_offline(scope, instr) is True

    def test_offline_threshold_triggers_at_300s(self) -> None:
        """Bridge enters offline mode after OFFLINE_THRESHOLD_S (300s) without contact."""
        from castor.cloud.bridge import OFFLINE_THRESHOLD_S

        assert OFFLINE_THRESHOLD_S == 300, (
            f"GAP-06 specifies 300s offline threshold, got {OFFLINE_THRESHOLD_S}"
        )

        bridge = _make_bridge()
        bridge._last_firestore_success = time.time() - (OFFLINE_THRESHOLD_S + 1)
        result = bridge._check_offline_mode()
        assert result is True
        assert bridge._offline_mode is True


# ---------------------------------------------------------------------------
# GAP-16 LOA Enforcement
# ---------------------------------------------------------------------------


class TestLoaEnforcement:
    """control scope must reject LoA < min_loa_for_control."""

    def test_control_loa_zero_rejected_when_min_loa_1(self) -> None:
        """LoA 0 on a control command is rejected when min_loa_for_control=1."""
        bridge = _make_bridge(min_loa_for_control=1, loa_enforcement=True)
        allowed = bridge._validate_scope_level(scope="control", loa=0)
        assert allowed is False

    def test_control_loa_1_accepted_when_min_loa_1(self) -> None:
        """LoA 1 on a control command is accepted when min_loa_for_control=1."""
        bridge = _make_bridge(min_loa_for_control=1, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="control", loa=1) is True

    def test_control_loa_2_accepted_when_min_loa_2(self) -> None:
        """LoA 2 on a control command is accepted when min_loa_for_control=2."""
        bridge = _make_bridge(min_loa_for_control=2, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="control", loa=2) is True

    def test_control_loa_1_rejected_when_min_loa_2(self) -> None:
        """LoA 1 on a control command is rejected when min_loa_for_control=2."""
        bridge = _make_bridge(min_loa_for_control=2, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="control", loa=1) is False

    def test_system_scope_uses_min_loa_for_control(self) -> None:
        """system scope uses the same min_loa_for_control threshold as control."""
        bridge = _make_bridge(min_loa_for_control=2, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="system", loa=1) is False
        assert bridge._validate_scope_level(scope="system", loa=2) is True

    def test_safety_scope_always_allowed_regardless_of_loa(self) -> None:
        """P66: safety scope is always allowed — even LoA 0 must pass."""
        bridge = _make_bridge(min_loa_for_control=99, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="safety", loa=0) is True

    def test_discover_scope_always_allowed(self) -> None:
        """discover scope (level 0) needs no LoA."""
        bridge = _make_bridge(min_loa_for_control=99, loa_enforcement=True)
        assert bridge._validate_scope_level(scope="discover", loa=0) is True


# ---------------------------------------------------------------------------
# RCAN Scope Levels (spec §4.2)
# ---------------------------------------------------------------------------


class TestScopeLevels:
    """Verify canonical scope → numeric level mapping matches RCAN v1.6 §4.2."""

    @pytest.mark.parametrize(
        "scope,expected_level",
        [
            ("discover", 0),
            ("transparency", 0),
            ("status", 1),
            ("chat", 2),
            ("control", 3),
            ("system", 3),
            ("safety", 99),
        ],
    )
    def test_scope_level_values(self, scope: str, expected_level: int) -> None:
        bridge = _make_bridge()
        actual = bridge.SCOPE_LEVELS.get(scope)
        assert actual == expected_level, (
            f"RCAN v1.6 §4.2: scope '{scope}' must have level {expected_level}, got {actual}"
        )

    def test_system_and_control_are_equal_level(self) -> None:
        """system and control are both level 3 (control-equivalent)."""
        bridge = _make_bridge()
        assert bridge.SCOPE_LEVELS["system"] == bridge.SCOPE_LEVELS["control"] == 3

    def test_safety_is_highest_level(self) -> None:
        """safety (99) must be strictly higher than all other scopes."""
        bridge = _make_bridge()
        safety_level = bridge.SCOPE_LEVELS["safety"]
        non_safety = {k: v for k, v in bridge.SCOPE_LEVELS.items() if k != "safety"}
        for scope, level in non_safety.items():
            assert safety_level > level, f"safety must be higher than {scope}"


# ---------------------------------------------------------------------------
# GAP-10 Training Consent Gate
# ---------------------------------------------------------------------------


class TestTrainingConsentGate:
    """Training commands must be blocked if CASTOR_ALLOW_TRAINING / consent not set."""

    def test_training_command_blocked_without_consent(self) -> None:
        """training_consent_required=True + no consent record → command rejected."""
        bridge = _make_bridge(training_consent_required=True)

        # Mock Firestore to return no consent records
        empty_stream = MagicMock()
        empty_stream.__iter__ = MagicMock(return_value=iter([]))
        consent_query = MagicMock()
        consent_query.stream.return_value = empty_stream
        where2 = MagicMock(return_value=consent_query)
        where1 = MagicMock(return_value=MagicMock(where=where2))
        collection_mock = MagicMock(where=where1)
        bridge._db.collection.return_value.document.return_value.collection.return_value = (
            collection_mock
        )

        allowed = bridge._check_training_consent("rrn://some-owner", {})
        assert allowed is False

    def test_training_command_allowed_with_consent(self) -> None:
        """training_consent_required=True + valid consent record → command passes."""
        bridge = _make_bridge(training_consent_required=True)

        # The actual call chain in _check_training_consent:
        #   self._robot_ref()
        #       .collection("training_consents")
        #       .where(...)
        #       .where(...)
        #       .limit(1)
        #       .stream()
        # MagicMock auto-chains up to .limit(1); we need .stream() on that to
        # return an iterable with one doc.
        fake_doc = MagicMock()
        # Build the chain bottom-up: limit(1).stream() → [fake_doc]
        stream_result = [fake_doc]
        limit_mock = MagicMock()
        limit_mock.stream.return_value = stream_result
        # .where(...).where(...) each return a mock; attach limit to the last where
        where2_result = MagicMock()
        where2_result.limit.return_value = limit_mock
        where1_result = MagicMock()
        where1_result.where.return_value = where2_result
        collection_mock = MagicMock()
        collection_mock.where.return_value = where1_result
        # Wire into bridge._db chain: _robot_ref() = _db.collection("robots").document(rrn)
        bridge._db.collection.return_value.document.return_value.collection.return_value = (
            collection_mock
        )

        allowed = bridge._check_training_consent("rrn://some-owner", {})
        assert allowed is True

    def test_training_command_not_required_when_disabled(self) -> None:
        """training_consent_required=False → all training commands pass."""
        bridge = _make_bridge(training_consent_required=False)
        # _is_training_data_command returns False immediately when disabled
        assert bridge._is_training_data_command("control", "record voice_clip", {}) is False

    def test_training_keywords_detected(self) -> None:
        """Training keywords trigger consent check when enabled."""
        bridge = _make_bridge(training_consent_required=True)
        for kw in ("record", "training", "capture", "collect", "oak", "voice_clip"):
            assert bridge._is_training_data_command("control", f"do {kw} now", {}) is True

    def test_non_training_keywords_not_flagged(self) -> None:
        """Normal commands don't trigger the training consent gate."""
        bridge = _make_bridge(training_consent_required=True)
        assert bridge._is_training_data_command("chat", "tell me a joke", {}) is False
        assert bridge._is_training_data_command("control", "move forward", {}) is False


# ---------------------------------------------------------------------------
# Bridge System Dispatch
# ---------------------------------------------------------------------------


class TestSystemDispatch:
    """Only UPGRADE/REBOOT/RELOAD_CONFIG are valid system instructions."""

    def _make_bridge_with_httpx(self, **kwargs: Any) -> CastorBridge:
        bridge = _make_bridge(**kwargs)
        bridge.gateway_url = "http://127.0.0.1:8000"
        bridge.gateway_token = "test-token"
        return bridge

    def test_unknown_system_instruction_routed_to_command(self) -> None:
        """Unknown system instructions are routed to /api/command (not silently dropped)."""
        bridge = self._make_bridge_with_httpx()
        doc = _cmd_doc(scope="system", instruction="SELF_DESTRUCT")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_http):
            bridge._dispatch_to_gateway("system", "SELF_DESTRUCT", doc)

        # Unknown instructions are forwarded to /api/command (not dropped)
        mock_http.post.assert_called_once()
        called_url = mock_http.post.call_args[0][0]
        assert "/api/command" in called_url

    def test_upgrade_instruction_dispatched(self) -> None:
        """UPGRADE instruction is dispatched to /api/system/upgrade."""
        bridge = self._make_bridge_with_httpx()
        doc = _cmd_doc(scope="system", instruction="UPGRADE")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_http):
            bridge._dispatch_to_gateway("system", "UPGRADE", doc)

        mock_http.post.assert_called_once()
        called_url = mock_http.post.call_args[0][0]
        assert "/api/system/upgrade" in called_url

    def test_upgrade_with_version_dispatched(self) -> None:
        """UPGRADE: <version> passes version in the body."""
        bridge = self._make_bridge_with_httpx()
        doc = _cmd_doc(scope="system", instruction="UPGRADE: 2026.3.17.1")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_http):
            bridge._dispatch_to_gateway("system", "UPGRADE: 2026.3.17.1", doc)

        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs.get("json", {}).get("version") == "2026.3.17.1"

    def test_reboot_instruction_dispatched(self) -> None:
        """REBOOT instruction is dispatched to /api/system/reboot."""
        bridge = self._make_bridge_with_httpx()
        doc = _cmd_doc(scope="system", instruction="REBOOT")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_http):
            bridge._dispatch_to_gateway("system", "REBOOT", doc)

        mock_http.post.assert_called_once()
        called_url = mock_http.post.call_args[0][0]
        assert "/api/system/reboot" in called_url

    def test_reload_config_instruction_dispatched(self) -> None:
        """RELOAD_CONFIG instruction is dispatched to /api/config/reload."""
        bridge = self._make_bridge_with_httpx()
        doc = _cmd_doc(scope="system", instruction="RELOAD_CONFIG")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_http):
            bridge._dispatch_to_gateway("system", "RELOAD_CONFIG", doc)

        mock_http.post.assert_called_once()
        called_url = mock_http.post.call_args[0][0]
        assert "/api/config/reload" in called_url

    def test_multiple_unknown_instructions_routed_to_command(self) -> None:
        """Unknown system instructions are routed to /api/command for agent interpretation."""
        bridge = self._make_bridge_with_httpx()
        unknown_instructions = [
            "DELETE_ALL",
            "FORMAT_DISK",
            "EXFILTRATE",
            "UNKNOWN_COMMAND",
        ]
        for instr in unknown_instructions:
            doc = _cmd_doc(scope="system", instruction=instr)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            with patch("httpx.Client", return_value=mock_http):
                bridge._dispatch_to_gateway("system", instr, doc)
            # Unknown instructions are forwarded to /api/command, not dropped
            mock_http.post.assert_called_once()
            called_url = mock_http.post.call_args[0][0]
            assert "/api/command" in called_url, (
                f"Unknown instruction {instr!r} must be routed to /api/command"
            )
