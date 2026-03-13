"""Mistral AI provider — mistral-large, mistral-small, codestral, mistral-nemo.

Uses the OpenAI-compatible REST API at https://api.mistral.ai/v1.
Env var: MISTRAL_API_KEY

Closes: https://github.com/craigm26/OpenCastor/issues/198
"""

import base64
import logging
import os
import time
from collections.abc import Iterator

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Mistral")

# Mistral models with vision / multi-modal support
_VISION_MODELS = {"pixtral-large-latest", "pixtral-12b-2409", "pixtral-large-2411"}
_BASE_URL = "https://api.mistral.ai/v1"


class MistralProvider(BaseProvider):
    """Mistral AI adapter (mistral-large-latest, mistral-small-latest, codestral-latest).

    Mistral's API is OpenAI-compatible; we use the openai SDK with a
    custom base_url.  Default model: ``mistral-large-latest``.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        from openai import OpenAI

        api_key = (
            os.getenv("MISTRAL_API_KEY") or config.get("api_key") or config.get("mistral_api_key")
        )
        if not api_key:
            raise ValueError("MISTRAL_API_KEY not found in environment or config")

        base_url = config.get("base_url", _BASE_URL)
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self._vision = self.model_name in _VISION_MODELS

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

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
        system = self.build_messaging_prompt(surface=surface) if is_blank else self.system_prompt

        try:
            if is_blank or not self._vision:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": instruction},
                ]
            else:
                b64 = base64.b64encode(image_bytes).decode()
                messages = [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ],
                    },
                ]

            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=512,
            )
            text = resp.choices[0].message.content or ""
            action = self._clean_json(text)
            self._log_usage(resp)
            return Thought(text, action)
        except Exception as exc:
            logger.error("Mistral error: %s", exc)
            return Thought(f"Error: {exc}", None)

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Iterator[str]:
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)
        system = self.build_messaging_prompt(surface=surface) if is_blank else self.system_prompt
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": instruction},
        ]

        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=512,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.error("Mistral stream error: %s", exc)
            yield f"Error: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_usage(self, resp) -> None:
        try:
            from castor.usage import get_tracker

            usage = getattr(resp, "usage", None)
            get_tracker().log_usage(
                provider="mistral",
                model=self.model_name,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
        except Exception:
            pass
