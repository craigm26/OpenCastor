import base64
import logging
import os
import time
from typing import Iterator

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

    def health_check(self) -> dict:
        """Cheap health probe: list models endpoint (no inference cost)."""
        t0 = time.time()
        try:
            self.client.models.list()
            return {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
            }

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

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
            try:
                from castor.usage import get_tracker

                _usage = getattr(response, "usage", None)
                get_tracker().log_usage(
                    provider="openai",
                    model=self.model_name,
                    prompt_tokens=getattr(_usage, "prompt_tokens", 0) if _usage else 0,
                    completion_tokens=getattr(_usage, "completion_tokens", 0) if _usage else 0,
                )
            except Exception:
                pass
            return Thought(text, action)
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return Thought(f"Error: {e}", None)

    def get_usage_stats(self) -> dict:
        """Return session-level token usage from runtime_stats."""
        try:
            from castor.runtime_stats import get_stats
            rs = get_stats()
            return {
                "prompt_tokens": rs.get("tokens_in", 0),
                "completion_tokens": rs.get("tokens_out", 0),
                "total_requests": rs.get("api_calls", 0),
            }
        except Exception:
            return {}

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Iterator[str]:
        """Stream tokens from the OpenAI model.

        Yields individual text chunks as they arrive.
        """
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)
        system = (
            self.build_messaging_prompt(surface=surface)
            if is_blank
            else self.system_prompt
        )

        try:
            if is_blank:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": instruction},
                ]
            else:
                b64_image = base64.b64encode(image_bytes).decode("utf-8")
                messages = [
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
                ]

            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=300,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield f"Error: {e}"
