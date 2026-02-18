"""Hugging Face Inference API provider for OpenCastor.

Supports both the free Inference API and Inference Endpoints.
Uses the ``huggingface_hub`` library which is the official client
and handles authentication via ``HF_TOKEN`` (or ``HUGGINGFACE_TOKEN``).

Models are referenced by their Hub ID, e.g. ``meta-llama/Llama-3.3-70B-Instruct``
or any vision-language model like ``llava-hf/llava-v1.6-mistral-7b-hf``.
"""

import base64
import logging
import os
from typing import Any, Dict

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.HuggingFace")

# Default model for vision+instruction tasks
DEFAULT_MODEL = "meta-llama/Llama-3.3-70B-Instruct"

# Models known to support vision (image+text) input
VISION_MODELS = {
    "llava-hf/llava-v1.6-mistral-7b-hf",
    "llava-hf/llava-v1.6-34b-hf",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen2.5-VL-3B-Instruct",
    "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
}


def _get_hf_token(config: Dict[str, Any]) -> str | None:
    """Resolve HF token from env or config."""
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or config.get("api_key")


class HuggingFaceProvider(BaseProvider):
    """Hugging Face Inference API adapter.

    Works with any text-generation model on the Hub.  For vision-capable
    models (LLaVA, Qwen-VL, etc.) images are sent as base64 payloads.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        try:
            from huggingface_hub import InferenceClient
        except ImportError as exc:
            raise ImportError(
                "huggingface_hub is required for the Hugging Face provider. "
                "Install it with: pip install huggingface-hub"
            ) from exc

        token = _get_hf_token(config)
        if not token:
            logger.warning(
                "No HF_TOKEN found — requests will use anonymous access. "
                "Rate limits are strict; set HF_TOKEN for reliable usage."
            )

        if self.model_name == "default-model":
            self.model_name = DEFAULT_MODEL

        self.is_vision = self.model_name in VISION_MODELS or config.get("vision_enabled", False)

        self.client = InferenceClient(
            model=self.model_name,
            token=token,
        )

        # Optional: Inference Endpoint URL override
        endpoint_url = config.get("endpoint_url")
        if endpoint_url:
            self.client = InferenceClient(
                model=endpoint_url,
                token=token,
            )

        logger.info(
            "HuggingFace provider ready — model=%s vision=%s",
            self.model_name,
            self.is_vision,
        )

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        try:
            if self.is_vision:
                return self._think_vision(image_bytes, instruction)
            else:
                return self._think_text(instruction)
        except Exception as e:
            logger.error("HuggingFace inference error: %s", e)
            return Thought(f"Error: {e}", None)

    def _think_vision(self, image_bytes: bytes, instruction: str) -> Thought:
        """Send image + instruction to a vision-language model."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        response = self.client.chat_completion(
            messages=[
                {"role": "system", "content": self.system_prompt},
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
            max_tokens=300,
        )

        text = response.choices[0].message.content
        action = self._clean_json(text)
        return Thought(text, action)

    def _think_text(self, instruction: str) -> Thought:
        """Text-only inference for non-vision models."""
        response = self.client.chat_completion(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": instruction},
            ],
            max_tokens=300,
        )

        text = response.choices[0].message.content
        action = self._clean_json(text)
        return Thought(text, action)

    def list_models(self, task: str = "text-generation", limit: int = 20):
        """List trending models for a given task from the Hub.

        Useful for discovery — e.g. finding new vision-language models
        or instruction-tuned LLMs that can serve as robot brains.
        """
        from huggingface_hub import HfApi

        api = HfApi(token=_get_hf_token(self.config))
        models = api.list_models(
            task=task,
            sort="trending",
            direction=-1,
            limit=limit,
        )
        return [
            {
                "id": m.id,
                "downloads": m.downloads,
                "likes": m.likes,
                "pipeline_tag": m.pipeline_tag,
            }
            for m in models
        ]
