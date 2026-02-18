"""
Lightweight Claude OAuth proxy for OpenCastor.

Converts Anthropic Messages API calls to use the Claude CLI's OAuth token
by exchanging it for a session token via the Claude platform API, then
making direct API calls with proper headers.

This avoids the claude-max-api-proxy (which wraps Claude Code CLI and
adds its own system prompt, making it unsuitable for robot brain use).

Usage:
    # As a library (used by AnthropicProvider):
    from castor.claude_proxy import ClaudeOAuthClient
    client = ClaudeOAuthClient(oauth_token)
    response = client.create_message(model, system, messages, max_tokens)

    # As a standalone proxy server:
    python -m castor.claude_proxy --port 3457
"""

import json
import logging
import os
import subprocess

logger = logging.getLogger("OpenCastor.ClaudeProxy")


class ClaudeOAuthClient:
    """Direct Claude API client using OAuth token via CLI subprocess.

    Instead of trying to use the OAuth token directly with the API
    (which returns 'OAuth authentication is currently not supported'),
    this calls `claude` CLI in non-interactive pipe mode with --print.
    The CLI handles OAuth token exchange internally.
    """

    def __init__(self, oauth_token: str | None = None):
        self.oauth_token = oauth_token or self._read_token()
        if not self.oauth_token:
            raise ValueError("No OAuth token available")

    @staticmethod
    def _read_token() -> str | None:
        """Read OAuth token from OpenCastor's store."""
        path = os.path.expanduser("~/.opencastor/anthropic-token")
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            return None

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> dict:
        """Create a message using Claude CLI as the transport.

        Returns a dict with 'content' (list of content blocks) matching
        the Anthropic Messages API response format.
        """
        # Build the prompt: system + user messages concatenated
        # Claude CLI -p mode takes a single prompt string
        prompt_parts = []
        if system:
            prompt_parts.append(f"<system>\n{system}\n</system>\n")

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text parts (skip images for CLI mode)
                text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                content = "\n".join(text_parts)
            prompt_parts.append(f"<{role}>\n{content}\n</{role}>\n")

        full_prompt = "\n".join(prompt_parts)

        env = {**os.environ}
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        # Remove any stale API key that would override OAuth
        env.pop("ANTHROPIC_API_KEY", None)

        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    full_prompt,
                    "--output-format",
                    "text",
                    "--model",
                    model,
                    "--max-turns",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            if result.returncode != 0:
                logger.error("Claude CLI error: %s", result.stderr[:200])
                return {"content": [{"type": "text", "text": f"Error: {result.stderr[:200]}"}]}

            text = result.stdout.strip()
            return {"content": [{"type": "text", "text": text}]}

        except subprocess.TimeoutExpired:
            logger.error("Claude CLI timed out")
            return {"content": [{"type": "text", "text": "Error: CLI timeout"}]}
        except Exception as e:
            logger.error("Claude CLI failed: %s", e)
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}


def check_cli_auth() -> bool:
    """Check if Claude CLI is authenticated with OAuth."""
    try:
        token_path = os.path.expanduser("~/.opencastor/anthropic-token")
        if not os.path.exists(token_path):
            return False
        with open(token_path) as f:
            token = f.read().strip()
        if not token.startswith("sk-ant-oat01-"):
            return False

        env = {**os.environ}
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env.pop("ANTHROPIC_API_KEY", None)

        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        data = json.loads(result.stdout)
        return data.get("loggedIn", False)
    except Exception:
        return False
