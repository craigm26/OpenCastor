"""llama.cpp provider for OpenCastor — local LLM inference.

Supports two backends:
  1. Ollama's OpenAI-compatible API (http://localhost:11434/v1) — easiest
  2. llama-cpp-python direct loading — fastest, no server needed

Config:
    provider: llamacpp
    model: gemma3:1b          # Ollama model name or GGUF path
    base_url: http://localhost:11434/v1   # default: Ollama endpoint
    # Or for direct GGUF:
    model: /path/to/model.gguf
    n_ctx: 2048
    n_gpu_layers: 0
"""

import json
import logging
import os
from typing import Any, Dict

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.LlamaCpp")


class LlamaCppProvider(BaseProvider):
    """Local LLM via llama.cpp (Ollama API or direct GGUF)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._direct_model = None
        self._use_ollama = True

        model = self.model_name
        base_url = config.get("base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))

        # If model path ends in .gguf, use direct llama-cpp-python
        if model.endswith(".gguf"):
            try:
                from llama_cpp import Llama

                n_ctx = config.get("n_ctx", 2048)
                n_gpu = config.get("n_gpu_layers", 0)
                self._direct_model = Llama(
                    model_path=model, n_ctx=n_ctx, n_gpu_layers=n_gpu, verbose=False
                )
                self._use_ollama = False
                logger.info(f"llama.cpp direct: {model} (ctx={n_ctx})")
            except ImportError as exc:
                raise ImportError(
                    "llama-cpp-python required for GGUF models. "
                    "Install: pip install llama-cpp-python"
                ) from exc
        else:
            # Use Ollama's OpenAI-compatible API
            self._base_url = base_url.rstrip("/")
            self._model = model

            # Pre-load model in Ollama to avoid cold start
            try:
                import urllib.request

                ollama_url = self._base_url.replace("/v1", "")
                req = urllib.request.Request(
                    f"{ollama_url}/api/generate",
                    data=json.dumps({"model": model, "prompt": "", "keep_alive": "10m"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=30)
                logger.info(f"Ollama model loaded: {model} at {base_url}")
            except Exception as e:
                logger.warning(f"Ollama pre-load failed ({e}), will load on first call")

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        try:
            if self._direct_model:
                return self._think_direct(instruction)
            else:
                return self._think_ollama(instruction)
        except Exception as e:
            logger.error(f"llama.cpp error: {e}")
            return Thought(f"Error: {e}", None)

    def _think_direct(self, instruction: str) -> Thought:
        """Direct llama-cpp-python inference."""
        prompt = f"<|system|>\n{self.system_prompt}<|end|>\n<|user|>\n{instruction}<|end|>\n<|assistant|>\n"
        output = self._direct_model(prompt, max_tokens=100, stop=["<|end|>", "\n\n"])
        text = output["choices"][0]["text"].strip()
        action = self._clean_json(text)
        return Thought(text, action)

    def _think_ollama(self, instruction: str) -> Thought:
        """Ollama OpenAI-compatible API."""
        import urllib.request

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": instruction},
            ],
            "max_tokens": 100,
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        text = data["choices"][0]["message"]["content"].strip()
        action = self._clean_json(text)
        return Thought(text, action)
