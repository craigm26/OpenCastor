"""Tests for castor.providers.consensus_provider.ConsensusProvider.

All child providers are mocked so no LLM calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_provider(action: dict, raw_text: str = "ok") -> MagicMock:
    """Create a mock BaseProvider that returns a fixed Thought."""
    p = MagicMock()
    p.think.return_value = Thought(raw_text, action)
    p.think_stream.return_value = iter([raw_text])
    p.health_check.return_value = {"ok": True, "mode": "mock"}
    p.get_usage_stats.return_value = {}
    p._caps = []
    p._robot_name = "robot"
    return p


def _make_consensus(providers: list, quorum: int = 2, timeout_ms: int = 5000):
    """Instantiate ConsensusProvider with mocked children."""
    config = {
        "provider": "consensus",
        "model": "consensus",
        "quorum": quorum,
        "timeout_ms": timeout_ms,
        "consensus_providers": [{"provider": "mock"}] * len(providers),
    }

    from castor.providers.consensus_provider import ConsensusProvider

    with patch("castor.providers.consensus_provider._get_child_provider") as mock_factory:
        mock_factory.side_effect = providers
        cp = ConsensusProvider(config)

    # Replace children after init so we can reference the same mocks
    cp._children = providers
    return cp


# ── Action agreement tests ────────────────────────────────────────────────────


class TestActionsAgree:
    def _t(self, action: dict) -> Thought:
        return Thought("", action)

    def test_stop_agrees_with_stop(self):
        from castor.providers.consensus_provider import _actions_agree

        assert _actions_agree(self._t({"type": "stop"}), self._t({"type": "stop"}))

    def test_stop_disagrees_with_move(self):
        from castor.providers.consensus_provider import _actions_agree

        assert not _actions_agree(
            self._t({"type": "stop"}), self._t({"type": "move", "linear": 0.5, "angular": 0})
        )

    def test_move_within_tolerance(self):
        from castor.providers.consensus_provider import _actions_agree

        a = self._t({"type": "move", "linear": 0.5, "angular": 0.1})
        b = self._t({"type": "move", "linear": 0.6, "angular": 0.2})
        assert _actions_agree(a, b)

    def test_move_outside_tolerance(self):
        from castor.providers.consensus_provider import _actions_agree

        a = self._t({"type": "move", "linear": 0.5, "angular": 0.0})
        b = self._t({"type": "move", "linear": -0.5, "angular": 0.0})
        assert not _actions_agree(a, b)

    def test_grip_open_agrees_with_open(self):
        from castor.providers.consensus_provider import _actions_agree

        assert _actions_agree(
            self._t({"type": "grip", "state": "open"}),
            self._t({"type": "grip", "state": "open"}),
        )

    def test_grip_open_disagrees_with_close(self):
        from castor.providers.consensus_provider import _actions_agree

        assert not _actions_agree(
            self._t({"type": "grip", "state": "open"}),
            self._t({"type": "grip", "state": "close"}),
        )

    def test_wait_agrees(self):
        from castor.providers.consensus_provider import _actions_agree

        assert _actions_agree(
            self._t({"type": "wait", "duration_ms": 1000}),
            self._t({"type": "wait", "duration_ms": 2000}),
        )

    def test_nav_waypoint_within_tolerance(self):
        from castor.providers.consensus_provider import _actions_agree

        a = self._t({"type": "nav_waypoint", "distance_m": 1.0, "heading_deg": 45})
        b = self._t({"type": "nav_waypoint", "distance_m": 1.1, "heading_deg": 60})
        assert _actions_agree(a, b)

    def test_nav_waypoint_outside_distance_tolerance(self):
        from castor.providers.consensus_provider import _actions_agree

        a = self._t({"type": "nav_waypoint", "distance_m": 1.0, "heading_deg": 0})
        b = self._t({"type": "nav_waypoint", "distance_m": 2.0, "heading_deg": 0})
        assert not _actions_agree(a, b)

    def test_nav_waypoint_outside_heading_tolerance(self):
        from castor.providers.consensus_provider import _actions_agree

        a = self._t({"type": "nav_waypoint", "distance_m": 1.0, "heading_deg": 0})
        b = self._t({"type": "nav_waypoint", "distance_m": 1.0, "heading_deg": 90})
        assert not _actions_agree(a, b)


# ── ConsensusProvider.think tests ─────────────────────────────────────────────


class TestConsensusThink:
    def test_unanimous_stop_returns_stop(self):
        """All three providers agree on stop → stop wins."""
        providers = [
            _mock_provider({"type": "stop"}),
            _mock_provider({"type": "stop"}),
            _mock_provider({"type": "stop"}),
        ]
        cp = _make_consensus(providers, quorum=2)
        result = cp.think(b"", "stop everything")
        assert result.action["type"] == "stop"

    def test_majority_move_wins(self):
        """2 of 3 providers say move → move action returned."""
        providers = [
            _mock_provider({"type": "move", "linear": 0.5, "angular": 0.0}),
            _mock_provider({"type": "move", "linear": 0.6, "angular": 0.05}),
            _mock_provider({"type": "stop"}),
        ]
        cp = _make_consensus(providers, quorum=2)
        result = cp.think(b"", "go forward")
        assert result.action["type"] == "move"

    def test_averaged_move_linear(self):
        """move linear values are averaged across agreeing voters."""
        providers = [
            _mock_provider({"type": "move", "linear": 0.4, "angular": 0.0}),
            _mock_provider({"type": "move", "linear": 0.6, "angular": 0.0}),
        ]
        cp = _make_consensus(providers, quorum=2)
        result = cp.think(b"", "go forward")
        assert result.action["type"] == "move"
        assert abs(result.action["linear"] - 0.5) < 0.01

    def test_no_quorum_falls_back_to_primary(self):
        """Each provider picks a different action → primary (index 0) wins."""
        providers = [
            _mock_provider({"type": "stop"}, raw_text="primary"),
            _mock_provider({"type": "move", "linear": 0.5, "angular": 0.0}),
            _mock_provider({"type": "grip", "state": "open"}),
        ]
        cp = _make_consensus(providers, quorum=2)
        result = cp.think(b"", "do something")
        # Primary (idx 0) is stop
        assert result.action["type"] == "stop"

    def test_safety_block_before_providers(self):
        """Prompt injection detected before any provider is queried."""
        providers = [_mock_provider({"type": "stop"})]
        cp = _make_consensus(providers, quorum=1)

        blocked = Thought("Blocked!", {"type": "stop", "reason": "prompt_injection_blocked"})
        with patch.object(cp, "_check_instruction_safety", return_value=blocked):
            result = cp.think(b"", "injected payload")

        # No child was actually called
        providers[0].think.assert_not_called()
        assert result.action.get("reason") == "prompt_injection_blocked"

    def test_caps_propagated_to_children(self):
        """_caps and _robot_name are pushed to all children before think()."""
        providers = [
            _mock_provider({"type": "stop"}),
            _mock_provider({"type": "stop"}),
        ]
        cp = _make_consensus(providers, quorum=2)
        cp._caps = ["nav", "vision"]
        cp._robot_name = "dave"
        cp.think(b"", "test")
        for p in providers:
            assert p._caps == ["nav", "vision"]
            assert p._robot_name == "dave"

    def test_child_exception_handled(self):
        """A crashing child provider does not crash the whole think() call."""
        bad = MagicMock()
        bad.think.side_effect = RuntimeError("GPU exploded")
        bad._caps = []
        bad._robot_name = "robot"
        good = _mock_provider({"type": "stop"})
        good2 = _mock_provider({"type": "stop"})

        cp = _make_consensus([bad, good, good2], quorum=2)
        result = cp.think(b"", "test")
        # Two good providers agree on stop — still reaches quorum
        assert result.action["type"] == "stop"


# ── think_stream ──────────────────────────────────────────────────────────────


class TestConsensusStream:
    def test_stream_yields_quorum_winner_text(self):
        """think_stream() runs parallel think() calls, applies quorum, streams winner chunks."""
        # Both providers return "stop" (non-merged) — quorum=2 reached
        providers = [
            _mock_provider({"type": "stop"}, raw_text="stopping now"),
            _mock_provider({"type": "stop"}, raw_text="stopping now"),
        ]
        cp = _make_consensus(providers, quorum=2)
        tokens = list(cp.think_stream(b"", "forward"))
        full = "".join(tokens)
        # Winner's raw_text is "stopping now" (first agreeing thought, non-move type)
        assert full == "stopping now"

    def test_stream_no_quorum_falls_back_to_primary(self):
        """When quorum is not reached, primary provider's text is streamed."""
        providers = [
            _mock_provider(
                {"type": "move", "linear": 0.5, "angular": 0.0}, raw_text="primary text"
            ),
            _mock_provider({"type": "stop"}, raw_text="secondary text"),
        ]
        cp = _make_consensus(providers, quorum=2)  # move:1, stop:1 → no quorum
        tokens = list(cp.think_stream(b"", "forward"))
        # Falls back to primary (idx 0), streaming its raw_text
        assert "".join(tokens) == "primary text"

    def test_stream_uses_think_not_think_stream(self):
        """think_stream() calls think() on children, not think_stream()."""
        providers = [_mock_provider({"type": "stop"})]
        cp = _make_consensus(providers, quorum=1)
        list(cp.think_stream(b"", "forward"))
        providers[0].think.assert_called_once()
        providers[0].think_stream.assert_not_called()

    def test_stream_safety_block(self):
        blocked = Thought("Blocked!", {"type": "stop"})
        providers = [_mock_provider({"type": "stop"})]
        cp = _make_consensus(providers, quorum=1)
        with patch.object(cp, "_check_instruction_safety", return_value=blocked):
            tokens = list(cp.think_stream(b"", "injected"))
        assert tokens == ["Blocked!"]
        providers[0].think.assert_not_called()


# ── health_check ──────────────────────────────────────────────────────────────


class TestConsensusHealthCheck:
    def test_all_healthy(self):
        providers = [
            _mock_provider({"type": "stop"}),
            _mock_provider({"type": "stop"}),
        ]
        cp = _make_consensus(providers, quorum=2)
        hc = cp.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "consensus"
        assert hc["children"] == 2

    def test_one_unhealthy(self):
        p1 = _mock_provider({"type": "stop"})
        p2 = _mock_provider({"type": "stop"})
        p2.health_check.return_value = {"ok": False, "error": "down"}
        cp = _make_consensus([p1, p2], quorum=2)
        hc = cp.health_check()
        assert hc["ok"] is False


# ── get_usage_stats ────────────────────────────────────────────────────────────


class TestConsensusUsageStats:
    def test_aggregates_children(self):
        p1 = _mock_provider({"type": "stop"})
        p1.get_usage_stats.return_value = {"total_tokens": 100}
        p2 = _mock_provider({"type": "stop"})
        p2.get_usage_stats.return_value = {"total_tokens": 200}
        cp = _make_consensus([p1, p2], quorum=2)
        stats = cp.get_usage_stats()
        assert stats["provider"] == "consensus"
        assert len(stats["children"]) == 2


# ── Factory registration ───────────────────────────────────────────────────────


class TestConsensusFactory:
    def test_get_provider_returns_consensus(self):
        """get_provider dispatches 'consensus' to ConsensusProvider."""
        from castor.providers import _builtin_get_provider
        from castor.providers.consensus_provider import ConsensusProvider

        config = {
            "provider": "consensus",
            "model": "consensus",
            "quorum": 1,
            "timeout_ms": 1000,
            "consensus_providers": [{"provider": "mock_child"}],
        }

        mock_child = _mock_provider({"type": "stop"})
        with patch("castor.providers.consensus_provider._get_child_provider") as mock_factory:
            mock_factory.return_value = mock_child
            cp = _builtin_get_provider(config)

        assert isinstance(cp, ConsensusProvider)

    def test_no_providers_raises(self):
        """ConsensusProvider raises ValueError if consensus_providers is empty."""
        from castor.providers.consensus_provider import ConsensusProvider

        config = {
            "provider": "consensus",
            "model": "consensus",
            "consensus_providers": [],
        }
        with pytest.raises(ValueError, match="consensus_providers"):
            ConsensusProvider(config)

    def test_all_providers_fail_raises(self):
        """ConsensusProvider raises ValueError when all children fail to init."""
        from castor.providers.consensus_provider import ConsensusProvider

        config = {
            "provider": "consensus",
            "model": "consensus",
            "consensus_providers": [{"provider": "broken"}],
        }
        with patch(
            "castor.providers.consensus_provider._get_child_provider",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ValueError, match="all child providers"):
                ConsensusProvider(config)
