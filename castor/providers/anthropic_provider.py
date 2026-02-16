import os
import base64
import logging

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Anthropic")


class AnthropicProvider(BaseProvider):
    """Anthropic Claude adapter. Optimized for complex reasoning and safety."""

    def __init__(self, config):
        super().__init__(config)
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment or config")
        self.client = anthropic.Anthropic(api_key=api_key)

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        try:
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=1024,
                system=self.system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64_image,
                                },
                            },
                            {"type": "text", "text": instruction},
                        ],
                    }
                ],
            )
            text = response.content[0].text
            action = self._clean_json(text)
            return Thought(text, action)
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return Thought(f"Error: {e}", None)
