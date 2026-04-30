"""Tests for #867 Bug A — brain error surfacing.

Covers four regression paths surfaced by Bob's first live pick_place run:

1. claude_proxy.ClaudeOAuthClient should not return raw_text="Error: " when CLI
   exits nonzero with empty stderr — stdout / exit code must surface so the
   operator can tell *why* (expired token, missing binary, etc.).

2. claude_proxy.ClaudeOAuthClient should include the exception class name when
   subprocess raises an exception whose ``str(e)`` is empty.

3. AnthropicProvider.think error path should include the exception class name.

4. /api/arm/pick_place must return HTTP 503 when the brain fails to plan
   (raw_text starts with "Error"), not HTTP 200 with an empty-phase log.
"""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, patch


# ── 1 + 2. claude_proxy.ClaudeOAuthClient ─────────────────────────────────────


class TestClaudeOAuthClientErrorSurfacing:
    def test_nonzero_returncode_with_empty_stderr_surfaces_stdout(self):
        """If `claude` exits nonzero with empty stderr, fall back to stdout."""
        from castor.claude_proxy import ClaudeOAuthClient

        client = ClaudeOAuthClient(oauth_token="sk-ant-oat01-test")

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = ""
        fake_result.stdout = "Error: Invalid API key · Please run /login"

        with patch("castor.claude_proxy.subprocess.run", return_value=fake_result):
            resp = client.create_message(
                model="claude-sonnet-4-6",
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        text = resp["content"][0]["text"]
        assert text != "Error: ", "regression: empty error swallow returned"
        assert "Invalid API key" in text or "exit code 1" in text or "/login" in text

    def test_nonzero_returncode_includes_exit_code_when_both_streams_empty(self):
        """When stderr AND stdout are empty, exit code itself must surface."""
        from castor.claude_proxy import ClaudeOAuthClient

        client = ClaudeOAuthClient(oauth_token="sk-ant-oat01-test")

        fake_result = MagicMock()
        fake_result.returncode = 127  # command not found
        fake_result.stderr = ""
        fake_result.stdout = ""

        with patch("castor.claude_proxy.subprocess.run", return_value=fake_result):
            resp = client.create_message(
                model="claude-sonnet-4-6",
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        text = resp["content"][0]["text"]
        assert text != "Error: "
        assert "127" in text  # the exit code itself

    def test_subprocess_exception_with_empty_str_includes_type_name(self):
        """If subprocess raises with empty str(e), the type name must surface."""
        from castor.claude_proxy import ClaudeOAuthClient

        class EmptyError(RuntimeError):
            def __str__(self) -> str:
                return ""

        client = ClaudeOAuthClient(oauth_token="sk-ant-oat01-test")

        with patch("castor.claude_proxy.subprocess.run", side_effect=EmptyError()):
            resp = client.create_message(
                model="claude-sonnet-4-6",
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        text = resp["content"][0]["text"]
        assert "EmptyError" in text, f"type name missing: {text!r}"


# ── 3. AnthropicProvider error path ───────────────────────────────────────────


class TestAnthropicProviderErrorPath:
    def test_think_via_cli_includes_exception_type_in_raw_text(self):
        """When _think_via_cli's underlying call raises, raw_text should name the type."""
        from castor.providers.anthropic_provider import AnthropicProvider

        config = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key": "sk-ant-oat01-fake",
        }

        with patch.object(
            AnthropicProvider, "_read_stored_token", return_value="sk-ant-oat01-fake"
        ):
            with patch("castor.claude_proxy.ClaudeOAuthClient") as mock_client_cls:
                mock_client = MagicMock()

                class WeirdAuth(Exception):
                    def __str__(self) -> str:
                        return ""

                mock_client.create_message.side_effect = WeirdAuth()
                mock_client_cls.return_value = mock_client

                provider = AnthropicProvider(config)

        # Force CLI path
        provider._use_cli = True
        provider._cli_client = mock_client
        provider._cached_system_blocks = "sys"

        thought = provider._think_via_cli("look at this", b"", "terminal")

        assert thought.raw_text.startswith("Error"), thought.raw_text
        assert "WeirdAuth" in thought.raw_text, f"type missing: {thought.raw_text!r}"


# ── 4. /api/arm/pick_place 503 when brain fails ───────────────────────────────


def _make_client_and_reset(monkeypatch):
    """Same helper pattern as tests/test_depth.py."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.offline_fallback = None
    api_mod.state.provider_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.API_TOKEN = None

    from starlette.testclient import TestClient

    from castor.api import app

    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    import contextlib as _contextlib

    @_contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    return TestClient(app, raise_server_exceptions=False)


class TestPickPlaceBrainFailure:
    def test_pick_place_returns_503_when_brain_returns_error_thought(self, monkeypatch):
        """When state.brain.think() returns Thought(raw_text='Error ...', action=None),
        /api/arm/pick_place must return 503, not 200 with empty-phase log.
        """
        client = _make_client_and_reset(monkeypatch)

        from castor.providers.base import Thought
        import castor.api as api_mod

        # Driver with arm capability — clears the existing 503 at line 2861
        mock_driver = MagicMock()
        mock_driver.set_joint_positions = MagicMock()
        api_mod.state.driver = mock_driver

        # Brain that always errors
        mock_brain = MagicMock()
        mock_brain.think.return_value = Thought(
            raw_text="Error [AuthenticationError]: token expired",
            action=None,
        )
        api_mod.state.brain = mock_brain

        # Provide a dummy frame so _vision_plan can advance to the brain call
        with patch("castor.api._capture_live_frame", return_value=b"\xff\xd8" + b"\x00" * 1024):
            resp = client.post(
                "/api/arm/pick_place",
                json={"target": "red lego", "destination": "bowl", "max_vision_steps": 1},
            )

        assert resp.status_code == 503, (
            f"expected 503 (brain-failed), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        # OpenCastor uses {"error": "...", "code": "HTTP_NNN"} per CLAUDE.md
        msg = body.get("error", "") or body.get("detail", "")
        assert "Error" in msg or "rain" in msg, body  # "Error" or "Brain"/"brain"
