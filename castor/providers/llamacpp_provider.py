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
    # For vision-capable GGUF (llava, bakllava, moondream):
    clip_model_path: /path/to/mmproj.gguf
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, Optional

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.LlamaCpp")

# Vision-capable model name fragments (matched case-insensitively)
_VISION_MODEL_HINTS = ("llava", "bakllava", "moondream", "minicpm-v", "qwen-vl", "cogvlm")


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------

class LlamaCppError(Exception):
    """Base exception for llama.cpp provider errors."""


class LlamaCppModelNotFoundError(LlamaCppError):
    """GGUF model file not found on disk."""


class LlamaCppConnectionError(LlamaCppError):
    """Cannot connect to the Ollama/llama.cpp server."""


class LlamaCppOOMError(LlamaCppError):
    """Ran out of memory loading or running the model."""


class LlamaCppProvider(BaseProvider):
    """Local LLM via llama.cpp (Ollama API or direct GGUF)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._direct_model = None
        self._use_ollama = True
        self._is_vision_model = False

        model = self.model_name
        base_url = config.get("base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))

        # If model path ends in .gguf, use direct llama-cpp-python
        if model.endswith(".gguf"):
            self._init_direct(model, config)
        else:
            self._init_ollama(model, base_url)

    def _init_direct(self, model: str, config: Dict[str, Any]) -> None:
        """Load a GGUF model directly via llama-cpp-python."""
        if not os.path.exists(model):
            raise LlamaCppModelNotFoundError(
                f"GGUF model not found: {model}\n"
                "Download a GGUF from HuggingFace and set model: /path/to/file.gguf"
            )
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python required for GGUF models. "
                "Install: pip install llama-cpp-python"
            ) from exc

        n_ctx = config.get("n_ctx", 2048)
        n_gpu = config.get("n_gpu_layers", 0)
        clip_path = config.get("clip_model_path")

        try:
            kwargs: Dict[str, Any] = {
                "model_path": model,
                "n_ctx": n_ctx,
                "n_gpu_layers": n_gpu,
                "verbose": False,
            }
            if clip_path:
                kwargs["clip_model_path"] = clip_path
                self._is_vision_model = True
                logger.info(f"llama.cpp vision model: {model} + clip={clip_path}")

            self._direct_model = Llama(**kwargs)
            self._use_ollama = False

            # Auto-detect vision capability from model name
            if not self._is_vision_model:
                low = os.path.basename(model).lower()
                self._is_vision_model = any(hint in low for hint in _VISION_MODEL_HINTS)

            logger.info(f"llama.cpp direct: {model} (ctx={n_ctx}, gpu_layers={n_gpu})")
        except MemoryError as exc:
            raise LlamaCppOOMError(
                f"Out of memory loading {model}. "
                "Try reducing n_ctx or n_gpu_layers, or use a smaller quantization (Q4_K_M)."
            ) from exc
        except Exception as exc:
            raise LlamaCppError(f"Failed to load {model}: {exc}") from exc

    def _init_ollama(self, model: str, base_url: str) -> None:
        """Set up Ollama API backend and pre-warm the model."""
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._is_vision_model = any(hint in model.lower() for hint in _VISION_MODEL_HINTS)

        # Pre-load model in Ollama to avoid cold start
        try:
            ollama_url = self._base_url.replace("/v1", "")
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=json.dumps({"model": model, "prompt": "", "keep_alive": "10m"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)
            logger.info(f"Ollama model loaded: {model} at {base_url}")
        except urllib.error.URLError as exc:
            raise LlamaCppConnectionError(
                f"Cannot reach Ollama at {base_url}. "
                "Is Ollama running? Start it with: ollama serve"
            ) from exc
        except Exception as exc:
            logger.warning(f"Ollama pre-load failed ({exc}), will load on first call")

    # ------------------------------------------------------------------
    # think()
    # ------------------------------------------------------------------

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        try:
            if self._direct_model:
                return self._think_direct(image_bytes, instruction)
            else:
                return self._think_ollama(image_bytes, instruction)
        except LlamaCppError:
            raise
        except urllib.error.URLError as exc:
            raise LlamaCppConnectionError(
                f"Lost connection to Ollama: {exc}"
            ) from exc
        except Exception as exc:
            logger.error(f"llama.cpp error: {exc}")
            return Thought(f"Error: {exc}", None)

    def _think_direct(self, image_bytes: bytes, instruction: str) -> Thought:
        """Direct llama-cpp-python inference, with optional vision."""
        if self._is_vision_model and image_bytes:
            # Pass image as base64 data URI in the prompt
            img_b64 = base64.b64encode(image_bytes).decode()
            prompt = (
                f"<|system|>\n{self.system_prompt}<|end|>\n"
                f"<|user|>\n<img src='data:image/jpeg;base64,{img_b64}'/>\n"
                f"{instruction}<|end|>\n<|assistant|>\n"
            )
        else:
            prompt = (
                f"<|system|>\n{self.system_prompt}<|end|>\n"
                f"<|user|>\n{instruction}<|end|>\n<|assistant|>\n"
            )
        output = self._direct_model(prompt, max_tokens=200, stop=["<|end|>", "\n\n"])
        text = output["choices"][0]["text"].strip()
        action = self._clean_json(text)
        return Thought(text, action)

    def _think_ollama(self, image_bytes: bytes, instruction: str) -> Thought:
        """Ollama OpenAI-compatible API, with optional vision."""
        user_content: Any
        if self._is_vision_model and image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            user_content = [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]
        else:
            user_content = instruction

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise LlamaCppConnectionError(
                f"Ollama unreachable at {self._base_url}: {exc}"
            ) from exc

        text = data["choices"][0]["message"]["content"].strip()
        action = self._clean_json(text)
        return Thought(text, action)

    # ------------------------------------------------------------------
    # think_stream()
    # ------------------------------------------------------------------

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
    ) -> Generator[str, None, Optional[Thought]]:
        """Yield response tokens one at a time, return final Thought.

        Usage::
            gen = provider.think_stream(img, "move forward")
            for token in gen:
                print(token, end="", flush=True)

        Yields:
            str: Individual response tokens as they are generated.

        Returns:
            The final Thought (via StopIteration.value) after all tokens.
        """
        if self._direct_model:
            return (yield from self._stream_direct(image_bytes, instruction))
        else:
            return (yield from self._stream_ollama(image_bytes, instruction))

    def _stream_direct(
        self, image_bytes: bytes, instruction: str
    ) -> Generator[str, None, Thought]:
        """Stream tokens from direct llama-cpp-python model."""
        if self._is_vision_model and image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            prompt = (
                f"<|system|>\n{self.system_prompt}<|end|>\n"
                f"<|user|>\n<img src='data:image/jpeg;base64,{img_b64}'/>\n"
                f"{instruction}<|end|>\n<|assistant|>\n"
            )
        else:
            prompt = (
                f"<|system|>\n{self.system_prompt}<|end|>\n"
                f"<|user|>\n{instruction}<|end|>\n<|assistant|>\n"
            )

        full_text = ""
        for chunk in self._direct_model(
            prompt,
            max_tokens=200,
            stop=["<|end|>", "\n\n"],
            stream=True,
        ):
            token = chunk["choices"][0].get("text", "")
            if token:
                full_text += token
                yield token

        action = self._clean_json(full_text)
        return Thought(full_text, action)

    def _stream_ollama(
        self, image_bytes: bytes, instruction: str
    ) -> Generator[str, None, Thought]:
        """Stream tokens from the Ollama API using SSE chunked responses."""
        user_content: Any
        if self._is_vision_model and image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            user_content = [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]
        else:
            user_content = instruction

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
            "stream": True,
        }

        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        full_text = ""
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        chunk = json.loads(line)
                        token = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if token:
                            full_text += token
                            yield token
                    except json.JSONDecodeError:
                        continue
        except urllib.error.URLError as exc:
            raise LlamaCppConnectionError(
                f"Ollama unreachable at {self._base_url}: {exc}"
            ) from exc

        action = self._clean_json(full_text)
        return Thought(full_text, action)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __del__(self):
        """Release llama-cpp-python model resources on garbage collection."""
        if self._direct_model is not None:
            try:
                # llama-cpp-python frees native memory on __del__ of the Llama object
                del self._direct_model
                self._direct_model = None
            except Exception:
                pass
