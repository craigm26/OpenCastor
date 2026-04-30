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
        system: "str | list[dict]",
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> dict:
        """Create a message using Claude CLI as the transport.

        Returns a dict with 'content' (list of content blocks) matching
        the Anthropic Messages API response format.

        ``system`` may be a plain string or a list of cache_control content
        blocks (``[{"type": "text", "text": "...", "cache_control": {...}}]``).
        The CLI transport concatenates the text of all blocks in order.
        """
        # Build the prompt: system + user messages concatenated
        # Claude CLI -p mode takes a single prompt string
        prompt_parts = []
        if system:
            if isinstance(system, list):
                # cache_control content blocks — extract and join text
                system_text = "\n".join(
                    block.get("text", "") for block in system if block.get("type") == "text"
                )
            else:
                system_text = system
            if system_text:
                prompt_parts.append(f"<system>\n{system_text}\n</system>\n")

        # Track whether any message has an image — used to enable Read tool
        _image_path = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text, and save any image to a temp file for Read tool access
                text_parts = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image" and _image_path is None:
                        try:
                            src = block.get("source", {})
                            if src.get("type") == "base64":
                                import base64 as _b64
                                import tempfile as _tmp

                                ext = "jpg" if "jpeg" in src.get("media_type", "") else "png"
                                tf = _tmp.NamedTemporaryFile(
                                    suffix=f".{ext}", delete=False, prefix="castor_frame_"
                                )
                                tf.write(_b64.b64decode(src["data"]))
                                tf.close()
                                _image_path = tf.name
                                logger.debug("Saved image to %s for CLI vision", _image_path)
                        except Exception as _ie:
                            logger.debug("Image save failed: %s", _ie)
                content = "\n".join(text_parts)
                # Append image reference so Claude knows to read it
                if _image_path and role == "user":
                    content = (
                        f"[Camera image saved to {_image_path} — read it to see the scene.]\n\n"
                        + content
                    )
            prompt_parts.append(f"<{role}>\n{content}\n</{role}>\n")

        full_prompt = "\n".join(prompt_parts)

        env = {**os.environ}
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        # Remove any stale API key that would override OAuth
        env.pop("ANTHROPIC_API_KEY", None)

        # Allow Read tool when there's an image file to analyse; otherwise
        # disable all tools so Claude doesn't try to run bash or edit files.
        allowed_tools = "Read" if _image_path else ""
        max_turns = "3" if _image_path else "1"

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
                    max_turns,
                    "--allowedTools",
                    allowed_tools,
                ],
                capture_output=True,
                text=True,
                timeout=90,
                env=env,
            )

            if result.returncode != 0:
                # Claude CLI often writes auth/runtime errors to stdout, not stderr;
                # fall back so the operator sees *why* the CLI failed (#867).
                err_msg = (
                    (result.stderr or "").strip()
                    or (result.stdout or "").strip()
                    or f"<no output, exit code {result.returncode}>"
                )
                logger.error("Claude CLI error (exit %d): %s", result.returncode, err_msg[:500])
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error [exit {result.returncode}]: {err_msg[:200]}",
                        }
                    ]
                }

            text = result.stdout.strip()
            return {"content": [{"type": "text", "text": text}]}

        except subprocess.TimeoutExpired:
            logger.error("Claude CLI timed out")
            return {"content": [{"type": "text", "text": "Error [TimeoutExpired]: CLI timeout"}]}
        except Exception as e:
            logger.error("Claude CLI failed [%s]: %s", type(e).__name__, e)
            return {"content": [{"type": "text", "text": f"Error [{type(e).__name__}]: {e!r}"}]}
        finally:
            # Clean up temp image file
            if _image_path:
                try:
                    import os as _os

                    _os.unlink(_image_path)
                except Exception:
                    pass


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
