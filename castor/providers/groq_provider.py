"""
Groq provider — ultra-low-latency cloud inference.

Uses Groq's OpenAI-compatible REST API.
Falls back to openai client with Groq base URL if `groq` SDK not installed.

Env:    GROQ_API_KEY
Install: pip install groq
Models:  llama-3.3-70b-versatile (default), llama-3.1-8b-instant,
         gemma2-9b-it, mixtral-8x7b-32768, whisper-large-v3
"""

import base64
import logging
import os
import time
from collections.abc import Iterator

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Groq")


class GroqProvider(BaseProvider):
    """Groq cloud inference provider — OpenAI-compatible API, sub-second latency."""

    def __init__(self, config):
        super().__init__(config)
        api_key = os.getenv("GROQ_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment or config")

        try:
            from groq import Groq

            self.client = Groq(api_key=api_key)
            self._sdk = "groq"
        except ImportError:
            from openai import OpenAI

            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            self._sdk = "openai-compat"
            logger.info("groq SDK not installed; using openai-compat mode")

    def health_check(self) -> dict:
        t0 = time.time()
        try:
            self.client.models.list()
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1), "error": None}
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
            }

    def think(self, image_bytes: bytes, instruction: str, surface: str = "whatsapp") -> Thought:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        messages = [{"role": "system", "content": self.system_prompt}]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": instruction})

        try:
            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=512,
            )
            raw = resp.choices[0].message.content or ""
            return Thought(raw_text=raw, action=self._clean_json(raw))
        except Exception as exc:
            logger.error("Groq think error: %s", exc)
            return Thought(raw_text=str(exc), action=None)

    def think_stream(
        self, image_bytes: bytes, instruction: str, surface: str = "whatsapp"
    ) -> Iterator[str]:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        messages = [{"role": "system", "content": self.system_prompt}]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": instruction})

        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=512,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
        except Exception as exc:
            logger.error("Groq stream error: %s", exc)
            yield str(exc)
