"""
OpenRouter provider — unified gateway to 100+ models via OpenAI-compatible API.

Routes requests to Claude, GPT-4o, Gemini, Llama, Mistral, and many more through
a single endpoint at https://openrouter.ai/api/v1. Uses the `openai` SDK with a
custom base_url and required OpenRouter-specific headers.

Env:     OPENROUTER_API_KEY  (required)
         OPENROUTER_MODEL    (optional override; default anthropic/claude-3.5-sonnet)
Install: pip install openai

Supported model families (examples):
  anthropic/claude-3.5-haiku, anthropic/claude-3.5-sonnet
  openai/gpt-4o, openai/gpt-4o-mini
  google/gemini-2.0-flash-001, google/gemini-flash-1.5
  meta-llama/llama-3.3-70b-instruct, meta-llama/llama-3.1-8b-instruct
  mistralai/mistral-7b-instruct, mistralai/mixtral-8x7b-instruct
  deepseek/deepseek-r1, deepseek/deepseek-chat
  qwen/qwen-2.5-72b-instruct
  Full list: https://openrouter.ai/models
"""

import base64
import logging
import os
import time
from collections.abc import Iterator

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.OpenRouter")

_DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"
_BASE_URL = "https://openrouter.ai/api/v1"
_EXTRA_HEADERS = {
    "HTTP-Referer": "https://opencastor.com",
    "X-Title": "OpenCastor",
}


class OpenRouterProvider(BaseProvider):
    """OpenRouter cloud provider — access 100+ models via one OpenAI-compatible endpoint."""

    def __init__(self, config: dict):
        super().__init__(config)
        api_key = os.getenv("OPENROUTER_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not found in environment or config")

        self.model_name = os.getenv("OPENROUTER_MODEL") or config.get("model", _DEFAULT_MODEL)

        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url=_BASE_URL,
            default_headers=_EXTRA_HEADERS,
        )
        logger.info("OpenRouterProvider ready (model=%s)", self.model_name)

    # ------------------------------------------------------------------ #
    # Health                                                               #
    # ------------------------------------------------------------------ #

    def health_check(self) -> dict:
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

    # ------------------------------------------------------------------ #
    # Inference                                                            #
    # ------------------------------------------------------------------ #

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
            logger.error("OpenRouter think error: %s", exc)
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
            logger.error("OpenRouter stream error: %s", exc)
            yield str(exc)

    # ------------------------------------------------------------------ #
    # Usage                                                                #
    # ------------------------------------------------------------------ #

    def get_usage_stats(self) -> dict:
        return {"provider": "openrouter", "model": self.model_name}
