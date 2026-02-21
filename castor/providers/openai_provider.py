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
        base_url = os.getenv("OPENAI_BASE_URL") or config.get("base_url")

        if not api_key and not base_url:
            raise ValueError("OPENAI_API_KEY not found in environment or config")

        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        else:
            # Some local proxies don't need a key
            kwargs["api_key"] = "not-needed"

        self.client = OpenAI(**kwargs)

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)
        system = (
            self.build_messaging_prompt(surface=surface)
            if is_blank
            else self.system_prompt
        )

        try:
            if is_blank:
                # Text-only: conversational messaging mode
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": instruction},
                ]
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    max_tokens=300,
                )
            else:
                # Vision mode: include frame
                b64_image = base64.b64encode(image_bytes).decode("utf-8")
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": instruction},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
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
