import base64
import logging
import os

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.OpenAI")


class OpenAIProvider(BaseProvider):
    """OpenAI GPT-4.1 adapter. Optimized for instruction following and vision."""

    def __init__(self, config):
        super().__init__(config)
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment or config")
        self.client = OpenAI(api_key=api_key)

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}"
                                },
                            },
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            text = response.choices[0].message.content
            action = self._clean_json(text)
            return Thought(text, action)
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return Thought(f"Error: {e}", None)
