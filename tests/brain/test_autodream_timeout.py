"""Tests for autoDream timeout fixes (issue #842).

Covers:
  - AnthropicProvider.think() passes timeout=60.0 to messages.create()
  - AnthropicProvider.think_stream() passes timeout=60.0 to messages.stream()
  - ClaudeOAuthClient.create_message() uses subprocess timeout=60
  - autodream_runner.main() catches TimeoutError and sys.exit(1)
  - scripts/autodream.sh unsets ANTHROPIC_API_KEY before python invocation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_anthropic_provider(model="claude-haiku-4-5-20251001"):
    """Build AnthropicProvider with a mocked anthropic.Anthropic client.

    Patches away token file reads so no real credentials are needed.
    """
    with (
        patch(
            "castor.providers.anthropic_provider.AnthropicProvider._read_stored_token",
            return_value=None,
        ),
        patch("anthropic.Anthropic") as MockAnthropic,
    ):
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client

        # Avoid CacheStats / prompt_cache imports blowing up
        with patch(
            "castor.providers.anthropic_provider.build_cached_system_prompt", return_value=[]
        ):
            from castor.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider({"model": model, "api_key": "sk-ant-test"})
        return provider, mock_client


# ── AnthropicProvider.think() ─────────────────────────────────────────────────


def test_think_passes_timeout_to_messages_create():
    """think() must pass timeout=60.0 so the LLM call cannot hang forever."""
    provider, mock_client = _make_anthropic_provider()

    mock_usage = MagicMock(
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        input_tokens=1,
        output_tokens=1,
    )
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="ok")]
    mock_response.usage = mock_usage
    mock_client.messages.create.return_value = mock_response

    provider.think(b"", "hello")

    assert mock_client.messages.create.called, "messages.create() was not called"
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs.get("timeout") == 60.0, (
        f"Expected timeout=60.0 in messages.create(), got: {call_kwargs.get('timeout')!r}"
    )


# ── AnthropicProvider.think_stream() ─────────────────────────────────────────


def test_think_stream_passes_timeout_to_messages_stream():
    """think_stream() must pass timeout=60.0 to messages.stream()."""
    provider, mock_client = _make_anthropic_provider()

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.text_stream = iter(["hello"])
    mock_client.messages.stream.return_value = mock_stream

    list(provider.think_stream(b"", "hello"))

    assert mock_client.messages.stream.called, "messages.stream() was not called"
    call_kwargs = mock_client.messages.stream.call_args.kwargs
    assert call_kwargs.get("timeout") == 60.0, (
        f"Expected timeout=60.0 in messages.stream(), got: {call_kwargs.get('timeout')!r}"
    )


# ── ClaudeOAuthClient subprocess timeout ─────────────────────────────────────


def test_claude_proxy_subprocess_timeout_is_60():
    """subprocess.run in ClaudeOAuthClient must use timeout=60 (not 30)."""
    from castor.claude_proxy import ClaudeOAuthClient

    client = ClaudeOAuthClient(oauth_token="sk-ant-oat01-test")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="reply", stderr="")
        client.create_message(
            model="claude-haiku-4-5-20251001",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert mock_run.called, "subprocess.run() was not called"
        timeout_used = mock_run.call_args.kwargs.get("timeout")
        assert timeout_used == 60, f"Expected subprocess timeout=60, got: {timeout_used!r}"


# ── autodream_runner TimeoutError handling ────────────────────────────────────


def test_runner_exits_1_on_timeout_error(tmp_path, monkeypatch):
    """brain.run() raising TimeoutError must cause sys.exit(1), not hang."""
    import castor.brain.autodream_runner as runner_mod

    monkeypatch.setattr(runner_mod, "DRY_RUN", False)
    monkeypatch.setattr(runner_mod, "OPENCASTOR_DIR", tmp_path)
    monkeypatch.setattr(runner_mod, "MEMORY_FILE", tmp_path / "robot-memory.md")
    monkeypatch.setattr(runner_mod, "DREAM_LOG_FILE", tmp_path / "dream-log.jsonl")
    monkeypatch.setattr(runner_mod, "GATEWAY_LOG", tmp_path / "gateway.log")

    mock_provider = MagicMock()
    mock_brain = MagicMock()
    mock_brain.run.side_effect = TimeoutError("provider timed out")

    with (
        patch("castor.providers.anthropic_provider.AnthropicProvider", return_value=mock_provider),
        patch("castor.brain.autodream_runner.AutoDreamBrain", return_value=mock_brain),
    ):
        with pytest.raises(SystemExit) as exc_info:
            runner_mod.main()

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) on TimeoutError, got exit code: {exc_info.value.code!r}"
    )


# ── scripts/autodream.sh unsets ANTHROPIC_API_KEY ────────────────────────────


def test_autodream_sh_unsets_anthropic_api_key():
    """autodream.sh must contain 'unset ANTHROPIC_API_KEY' before the python call.

    A stale ANTHROPIC_API_KEY in the environment can shadow the stored
    setup-token, causing authentication failures (issue #842).
    """
    script = Path(__file__).parents[2] / "scripts" / "autodream.sh"
    assert script.exists(), f"Script not found: {script}"
    content = script.read_text()
    assert "unset ANTHROPIC_API_KEY" in content, (
        "scripts/autodream.sh must contain 'unset ANTHROPIC_API_KEY' "
        "before the python invocation to prevent stale env key from "
        "shadowing the stored setup-token"
    )
