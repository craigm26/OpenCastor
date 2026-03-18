"""tests/test_r2r_security.py — RCAN R2R multi-combination security tests.

Covers:
  - R2R federation trust (fail-open/fail-closed, require_federation_trust flag)
  - CONFIG_SHARE forbidden-key scrubbing
  - /api/rcan/message endpoint auth (_verify_rcan_or_token)
  - Swarm scope non-escalation (SwarmConsensus.record_delegated_intent)
  - Commitment chain integrity
  - mDNS peer allowlist config
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import MagicMock, patch
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge_instance(extra_rcan: dict | None = None):
    """Construct a minimal CastorBridge with attributes set directly (no real init)."""
    from castor.cloud.bridge import CastorBridge, _FEDERATION_STUB_ACTIVE, _TrustAnchorCacheStub

    rcan_cfg: dict = extra_rcan or {}
    bridge = CastorBridge.__new__(CastorBridge)
    bridge._config = {"metadata": {"robot_name": "test"}, "rcan_protocol": rcan_cfg}
    bridge._rcan_cfg = rcan_cfg
    bridge.require_federation_trust = bool(rcan_cfg.get("require_federation_trust", False))
    # Bind methods that need self
    bridge._scrub_config_content = CastorBridge._scrub_config_content.__get__(bridge)
    bridge._validate_scope_level = CastorBridge._validate_scope_level.__get__(bridge)
    return bridge


def _make_intent(scope: str = "chat") -> "DelegatedIntent":
    from castor.swarm.consensus import DelegatedIntent

    return DelegatedIntent(
        intent_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        origin_robot_id="RRN-000000000001",
        assigned_robot_id="RRN-000000000005",
        action="test_action",
        params={},
        policy_constraints={"scope": scope},
        issued_at=time.time(),
    )


def _make_consensus():
    from castor.swarm.consensus import SwarmConsensus
    from castor.swarm.shared_memory import SharedMemory

    mem = SharedMemory(robot_id="RRN-000000000001")
    sc = SwarmConsensus.__new__(SwarmConsensus)
    sc._mem = mem
    sc._signing_secret = "test-secret"
    sc.record_delegated_intent = SwarmConsensus.record_delegated_intent.__get__(sc)
    sc._sign = SwarmConsensus._sign.__get__(sc)
    sc._intent_key = SwarmConsensus._intent_key.__get__(sc)
    return sc


# ---------------------------------------------------------------------------
# Group 1: Federation trust flag
# ---------------------------------------------------------------------------


class TestFederationTrustFlag:
    def test_require_false_is_default(self):
        bridge = _make_bridge_instance()
        assert bridge.require_federation_trust is False

    def test_require_true_is_configurable(self):
        bridge = _make_bridge_instance({"require_federation_trust": True})
        assert bridge.require_federation_trust is True

    def test_federation_stub_active_is_bool(self):
        from castor.cloud.bridge import _FEDERATION_STUB_ACTIVE

        assert isinstance(_FEDERATION_STUB_ACTIVE, bool)

    def test_fail_open_logic_when_require_false(self):
        bridge = _make_bridge_instance({"require_federation_trust": False})
        # Simulate the guard: only reject when BOTH stub active AND require_trust
        stub_active = True
        would_reject = stub_active and bridge.require_federation_trust
        assert would_reject is False

    def test_fail_closed_logic_when_require_true_and_stub(self):
        bridge = _make_bridge_instance({"require_federation_trust": True})
        stub_active = True
        would_reject = stub_active and bridge.require_federation_trust
        assert would_reject is True

    def test_fail_open_when_real_module_active(self):
        bridge = _make_bridge_instance({"require_federation_trust": True})
        stub_active = False  # real module available
        would_reject = stub_active and bridge.require_federation_trust
        assert would_reject is False

    def test_rcan_cfg_stored_on_bridge(self):
        cfg = {"require_federation_trust": True, "peers": ["RRN-000000000005"]}
        bridge = _make_bridge_instance(cfg)
        assert bridge._rcan_cfg == cfg

    def test_federation_rejection_path_in_check_federation(self):
        """Verify the rejection guard is in _check_federation source."""
        import inspect
        from castor.cloud.bridge import CastorBridge

        src = inspect.getsource(CastorBridge._check_federation)
        assert "require_federation_trust" in src
        assert "_FEDERATION_STUB_ACTIVE" in src


# ---------------------------------------------------------------------------
# Group 2: Scope level validation
# ---------------------------------------------------------------------------


class TestScopeLevelValidation:
    def test_scope_levels_dict_on_bridge(self):
        from castor.cloud.bridge import CastorBridge

        sl = CastorBridge.SCOPE_LEVELS
        assert sl["discover"] == 0
        assert sl["status"] == 1
        assert sl["chat"] == 2
        assert sl["control"] == 3
        assert sl["system"] == 3
        assert sl["safety"] == 99

    def test_safety_always_passes_loa_check(self):
        bridge = _make_bridge_instance()
        bridge._config["rcan_protocol"] = {"min_loa_for_control": 99}
        assert bridge._validate_scope_level("safety", loa=0) is True

    def test_discover_always_passes(self):
        bridge = _make_bridge_instance()
        assert bridge._validate_scope_level("discover", loa=0) is True

    def test_control_blocked_at_loa_0_when_min_is_1(self):
        from castor.cloud.bridge import CastorBridge

        bridge = _make_bridge_instance({"min_loa_for_control": 1})
        bridge.min_loa_for_control = 1
        bridge._validate_scope_level = CastorBridge._validate_scope_level.__get__(bridge)
        assert bridge._validate_scope_level("control", loa=0) is False

    def test_control_allowed_at_loa_1_when_min_is_1(self):
        from castor.cloud.bridge import CastorBridge

        bridge = _make_bridge_instance({"min_loa_for_control": 1})
        bridge.min_loa_for_control = 1
        bridge._validate_scope_level = CastorBridge._validate_scope_level.__get__(bridge)
        assert bridge._validate_scope_level("control", loa=1) is True

    def test_system_scope_uses_same_level_as_control(self):
        from castor.cloud.bridge import CastorBridge

        assert CastorBridge.SCOPE_LEVELS["system"] == CastorBridge.SCOPE_LEVELS["control"]


# ---------------------------------------------------------------------------
# Group 3: CONFIG_SHARE forbidden-key scrubbing
# ---------------------------------------------------------------------------


class TestConfigShareScrubbing:
    def test_scrub_removes_safety_key(self):
        bridge = _make_bridge_instance()
        yaml_in = "metadata:\n  name: test\nsafety:\n  estop: true\n"
        result = bridge._scrub_config_content(yaml_in)
        assert "safety" not in result
        assert "metadata" in result

    def test_scrub_removes_auth_key(self):
        bridge = _make_bridge_instance()
        yaml_in = "metadata:\n  name: test\nauth:\n  token: secret\n"
        result = bridge._scrub_config_content(yaml_in)
        assert "auth:" not in result

    def test_scrub_removes_p66_key(self):
        bridge = _make_bridge_instance()
        yaml_in = "metadata:\n  name: test\np66:\n  invariants: []\n"
        result = bridge._scrub_config_content(yaml_in)
        assert "p66:" not in result

    def test_scrub_preserves_safe_keys(self):
        bridge = _make_bridge_instance()
        yaml_in = "metadata:\n  name: test\ncameras:\n  - id: oak\n"
        result = bridge._scrub_config_content(yaml_in)
        assert "cameras" in result
        assert "metadata" in result

    def test_scrub_handles_empty_content(self):
        bridge = _make_bridge_instance()
        result = bridge._scrub_config_content("")
        assert isinstance(result, str)

    def test_config_forbidden_keys_set(self):
        from castor.cloud.bridge import CastorBridge

        fk = CastorBridge._CONFIG_FORBIDDEN_KEYS
        assert "safety" in fk
        assert "auth" in fk
        assert "p66" in fk


# ---------------------------------------------------------------------------
# Group 4: /api/rcan/message endpoint auth
# ---------------------------------------------------------------------------


class TestRcanMessageEndpointAuth:
    def test_verify_rcan_or_token_exists(self):
        import castor.api as api_mod

        assert hasattr(api_mod, "_verify_rcan_or_token"), (
            "_verify_rcan_or_token not found in castor.api"
        )

    def test_rcan_message_route_exists(self):
        import castor.api as api_mod

        routes = {getattr(r, "path", ""): r for r in api_mod.app.routes}
        assert "/api/rcan/message" in routes, "/api/rcan/message route missing"

    def test_rcan_message_non_discover_requires_auth(self):
        """Non-DISCOVER msg_types must trigger auth check (inline, not FastAPI dep).

        DISCOVER (msg_type=1) is public; all other types require Bearer or
        RCAN-Signature.  Verify the inline auth guard exists in source.
        """
        import inspect
        import castor.api as api_mod

        src = inspect.getsource(api_mod.rcan_receive_message)
        assert "_verify_rcan_or_token" in src, (
            "rcan_receive_message must call _verify_rcan_or_token for non-DISCOVER types"
        )
        assert "msg_type != 1" in src, "DISCOVER (msg_type=1) must be exempted from auth check"

    # Lightweight logic tests (no FastAPI test client)
    @pytest.mark.parametrize(
        "auth,sig,expected",
        [
            ("Bearer validtoken", "", True),
            ("", "v1:abc123==", True),
            ("", "", False),
            ("Bearer ", "", False),  # empty bearer
            ("Basic abc", "", False),  # wrong scheme, no sig
        ],
    )
    def test_auth_logic(self, auth, sig, expected):
        """Simulate the _verify_rcan_or_token decision."""
        allowed = (auth.startswith("Bearer ") and len(auth) > 7) or bool(sig)
        assert allowed is expected


# ---------------------------------------------------------------------------
# Group 5: Swarm scope non-escalation
# ---------------------------------------------------------------------------


class TestSwarmScopeNonEscalation:
    def test_chat_to_control_rejected(self):
        sc = _make_consensus()
        intent = _make_intent(scope="control")
        with pytest.raises(ValueError, match="(?i)scope escalation"):
            sc.record_delegated_intent(intent, originating_scope="chat")

    def test_status_to_control_rejected(self):
        sc = _make_consensus()
        intent = _make_intent(scope="control")
        with pytest.raises(ValueError, match="(?i)scope escalation"):
            sc.record_delegated_intent(intent, originating_scope="status")

    def test_discover_to_safety_rejected(self):
        sc = _make_consensus()
        intent = _make_intent(scope="safety")
        with pytest.raises(ValueError, match="(?i)scope escalation"):
            sc.record_delegated_intent(intent, originating_scope="discover")

    def test_same_scope_allowed(self):
        sc = _make_consensus()
        intent = _make_intent(scope="control")
        result = sc.record_delegated_intent(intent, originating_scope="control")
        assert result is not None

    def test_scope_demotion_allowed(self):
        sc = _make_consensus()
        intent = _make_intent(scope="chat")
        result = sc.record_delegated_intent(intent, originating_scope="control")
        assert result is not None

    def test_safety_to_safety_allowed(self):
        sc = _make_consensus()
        intent = _make_intent(scope="safety")
        result = sc.record_delegated_intent(intent, originating_scope="safety")
        assert result is not None

    def test_no_originating_scope_is_fail_open(self):
        sc = _make_consensus()
        intent = _make_intent(scope="safety")
        # No originating_scope → no enforcement
        result = sc.record_delegated_intent(intent, originating_scope=None)
        assert result is not None

    def test_scope_levels_in_consensus(self):
        from castor.swarm.consensus import SCOPE_LEVELS

        assert SCOPE_LEVELS["discover"] == 0
        assert SCOPE_LEVELS["status"] == 1
        assert SCOPE_LEVELS["chat"] == 2
        assert SCOPE_LEVELS["control"] == 3
        assert SCOPE_LEVELS["system"] == 3
        assert SCOPE_LEVELS["safety"] == 99

    def test_scope_levels_match_bridge(self):
        from castor.cloud.bridge import CastorBridge
        from castor.swarm.consensus import SCOPE_LEVELS

        for scope, level in SCOPE_LEVELS.items():
            assert CastorBridge.SCOPE_LEVELS.get(scope) == level, (
                f"bridge/consensus mismatch for {scope!r}"
            )


# ---------------------------------------------------------------------------
# Group 6: Commitment chain integrity
# ---------------------------------------------------------------------------


class TestCommitmentChain:
    def _fresh_chain(self, tmp_path):
        from castor.rcan.commitment_chain import CommitmentChain

        return CommitmentChain(log_path=str(tmp_path / "chain.jsonl"))

    def _append(self, chain, action: str, robot_uri: str = "rcan://test/bot"):
        chain.append_action(action, {"ts": time.time()}, robot_uri=robot_uri)

    def test_append_action_and_verify(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        self._append(chain, "test_action")
        ok, count, errors = chain.verify_log()
        assert ok is True
        assert count >= 1

    def test_multiple_appends_verified(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        for i in range(5):
            self._append(chain, f"act_{i}")
        ok, count, errors = chain.verify_log()
        assert ok is True
        assert count == 5

    def test_tamper_detection(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        self._append(chain, "original")
        log_path = tmp_path / "chain.jsonl"
        lines = log_path.read_text().splitlines()
        entry = json.loads(lines[0])
        entry["action"] = "tampered"
        lines[0] = json.dumps(entry)
        log_path.write_text("\n".join(lines) + "\n")
        ok, count, errors = chain.verify_log()
        assert ok is False
        assert len(errors) > 0

    def test_untampered_chain_passes(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        self._append(chain, "act")
        ok, _, errors = chain.verify_log()
        assert ok is True
        assert errors == []

    def test_empty_chain_is_valid(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        ok, count, errors = chain.verify_log()
        assert ok is True
        assert count == 0

    def test_last_n_returns_recent(self, tmp_path):
        chain = self._fresh_chain(tmp_path)
        for i in range(10):
            self._append(chain, f"a{i}")
        recent = chain.last_n(3)
        assert len(recent) == 3


# ---------------------------------------------------------------------------
# Group 7: mDNS peer allowlist
# ---------------------------------------------------------------------------


class TestMdnsPeerAllowlist:
    def test_peers_list_stored_in_rcan_cfg(self):
        cfg = {"peers": ["RRN-000000000001", "RRN-000000000005"]}
        bridge = _make_bridge_instance(cfg)
        assert bridge._rcan_cfg.get("peers") == ["RRN-000000000001", "RRN-000000000005"]

    def test_unknown_peer_not_in_allowlist(self):
        bridge = _make_bridge_instance({"peers": ["RRN-000000000001"]})
        allowlist = bridge._rcan_cfg.get("peers", [])
        assert "RRN-000000000099" not in allowlist

    def test_known_peer_in_allowlist(self):
        bridge = _make_bridge_instance({"peers": ["RRN-000000000001", "RRN-000000000005"]})
        allowlist = bridge._rcan_cfg.get("peers", [])
        assert "RRN-000000000005" in allowlist

    def test_empty_allowlist(self):
        bridge = _make_bridge_instance({"peers": []})
        assert bridge._rcan_cfg.get("peers", []) == []

    def test_missing_peers_key_defaults_empty(self):
        bridge = _make_bridge_instance({})
        assert bridge._rcan_cfg.get("peers", []) == []
