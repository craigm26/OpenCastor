import base64
import logging
import os

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Anthropic")


class AnthropicProvider(BaseProvider):
    """Anthropic Claude adapter. Optimized for complex reasoning and safety."""

    # Default to the latest Claude model when none specified in config
    DEFAULT_MODEL = "claude-opus-4-6"

    def __init__(self, config):
        # Apply default model before super().__init__ reads it
        if not config.get("model") or config.get("model") == "default-model":
            config["model"] = self.DEFAULT_MODEL
        super().__init__(config)
        import anthropic

        auth_mode = os.getenv("ANTHROPIC_AUTH_MODE", "").lower()

        if auth_mode == "oauth":
            # Claude Max/Pro plan — use OAuth via claude CLI credentials
            auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
            if not auth_token:
                # Read from Claude CLI stored credentials
                auth_token = self._read_claude_oauth_token()
            if auth_token:
                self.client = anthropic.Anthropic(
                    api_key=auth_token,
                    default_headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
                )
                logger.info("Using Claude OAuth credentials (Max/Pro plan)")
            else:
                raise ValueError(
                    "Claude OAuth token not found. Run 'claude auth login' to sign in, "
                    "or set ANTHROPIC_API_KEY in .env for API key auth."
                )
        else:
            # Standard API key auth
            api_key = os.getenv("ANTHROPIC_API_KEY") or config.get("api_key")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not found. Set it in .env or run "
                    "'castor wizard' to authenticate with your Claude Max plan."
                )
            self.client = anthropic.Anthropic(api_key=api_key)

    @staticmethod
    def _read_claude_oauth_token():
        """Read OAuth access token from Claude CLI credentials file."""
        import json

        creds_path = os.path.expanduser("~/.claude/.credentials.json")
        try:
            if os.path.exists(creds_path):
                with open(creds_path) as f:
                    data = json.load(f)
                oauth = data.get("claudeAiOauth", {})
                token = oauth.get("accessToken")
                if token:
                    logger.debug("Read OAuth token from Claude CLI credentials")
                    return token
        except Exception as e:
            logger.debug(f"Could not read Claude credentials: {e}")
        return None

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Build message content -- include image only if it has real data
        content = []
        is_blank = image_bytes == b"\x00" * len(image_bytes)
        if not is_blank and len(image_bytes) > 100:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64_image,
                    },
                }
            )
        content.append({"type": "text", "text": instruction})

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=1024,
                system=self.system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            text = response.content[0].text
            action = self._clean_json(text)
            return Thought(text, action)
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return Thought(f"Error: {e}", None)
