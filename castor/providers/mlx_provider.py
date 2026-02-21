"""MLX provider for OpenCastor — Apple Silicon native inference.

Supports three backends:
  1. mlx-lm direct (pip install mlx-lm) — fastest, no server needed
  2. vLLM-MLX server (OpenAI-compatible API at localhost)
  3. MLX-OpenAI-Server or any MLX-based OpenAI-compatible endpoint

On Apple Silicon Macs, MLX uses the unified memory GPU for 100-400+ tok/s
inference — dramatically faster than CPU-only solutions like Ollama.

Config examples:
    # Direct mlx-lm (no server)
    provider: mlx
    model: mlx-community/Qwen2.5-7B-Instruct-4bit

    # vLLM-MLX or MLX-OpenAI-Server
    provider: mlx
    model: Qwen/Qwen2.5-7B-Instruct
    base_url: http://localhost:8000/v1

    # Vision model
    provider: mlx
    model: mlx-community/Qwen2.5-VL-7B-Instruct-4bit
    vision_enabled: true
"""

import base64
import json
import logging
import os
from typing import Any, Dict, Generator, Optional

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.MLX")

# Models known to support vision via mlx-vlm
VISION_MODELS = {
    "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
    "mlx-community/Qwen2.5-VL-3B-Instruct-4bit",
    "mlx-community/llava-v1.6-mistral-7b-4bit",
    "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
}


class MLXProvider(BaseProvider):
    """Apple Silicon native inference via MLX.

    Auto-detects whether to use direct mlx-lm or an OpenAI-compatible
    server (vLLM-MLX, MLX-OpenAI-Server).
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._use_server = False
        self._mlx_model = None
        self._mlx_tokenizer = None

        base_url = config.get("base_url", os.getenv("MLX_BASE_URL", ""))

        if base_url:
            # Server mode (vLLM-MLX, MLX-OpenAI-Server)
            self._use_server = True
            self._base_url = base_url.rstrip("/")
            logger.info(f"MLX server mode: {self.model_name} at {self._base_url}")
        else:
            # Direct mlx-lm mode
            try:
                from mlx_lm import load

                self._mlx_model, self._mlx_tokenizer = load(self.model_name)
                logger.info(f"MLX direct mode: {self.model_name} loaded")
            except ImportError as exc:
                raise ImportError(
                    "mlx-lm required for direct MLX inference. "
                    "Install: pip install mlx-lm\n"
                    "Or use server mode with base_url config."
                ) from exc

        self.is_vision = self.model_name in VISION_MODELS or config.get("vision_enabled", False)

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        try:
            if self._use_server:
                return self._think_server(image_bytes, instruction)
            else:
                return self._think_direct(instruction)
        except Exception as e:
            logger.error(f"MLX error: {e}")
            return Thought(f"Error: {e}", None)

    def _think_direct(self, instruction: str) -> Thought:
        """Direct mlx-lm inference (no server)."""
        from mlx_lm import generate

        prompt = f"<|system|>\n{self.system_prompt}<|end|>\n<|user|>\n{instruction}<|end|>\n<|assistant|>\n"
        text = generate(
            self._mlx_model,
            self._mlx_tokenizer,
            prompt=prompt,
            max_tokens=150,
        )
        action = self._clean_json(text)
        return Thought(text, action)

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
    ) -> Generator[str, None, Optional[Thought]]:
        """Yield response tokens one at a time, return final Thought.

        Works in both server mode (SSE streaming) and direct mode
        (mlx_lm.stream_generate()).

        Usage::
            gen = provider.think_stream(img, "move forward")
            for token in gen:
                print(token, end="", flush=True)
        """
        try:
            if self._use_server:
                return (yield from self._stream_server(image_bytes, instruction))
            else:
                return (yield from self._stream_direct(instruction))
        except Exception as e:
            logger.error(f"MLX stream error: {e}")
            yield f"Error: {e}"
            return Thought(f"Error: {e}", None)

    def _stream_server(
        self, image_bytes: bytes, instruction: str
    ) -> Generator[str, None, Thought]:
        """Stream tokens from an OpenAI-compatible server via SSE."""
        import urllib.request

        messages = [{"role": "system", "content": self.system_prompt}]
        if self.is_vision and image_bytes and len(image_bytes) > 1024:
            b64 = base64.b64encode(image_bytes).decode()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": instruction})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": 150,
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
            with urllib.request.urlopen(req, timeout=30) as resp:
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
        except Exception as e:
            logger.error(f"MLX server stream error: {e}")
            raise

        action = self._clean_json(full_text)
        return Thought(full_text, action)

    def _stream_direct(self, instruction: str) -> Generator[str, None, Thought]:
        """Stream tokens from direct mlx-lm using stream_generate."""
        prompt = (
            f"<|system|>\n{self.system_prompt}<|end|>\n"
            f"<|user|>\n{instruction}<|end|>\n<|assistant|>\n"
        )
        full_text = ""
        try:
            from mlx_lm import stream_generate

            for token in stream_generate(
                self._mlx_model,
                self._mlx_tokenizer,
                prompt=prompt,
                max_tokens=150,
            ):
                full_text += token
                yield token
        except ImportError:
            # Fallback: emit entire response as single token
            from mlx_lm import generate

            text = generate(
                self._mlx_model,
                self._mlx_tokenizer,
                prompt=prompt,
                max_tokens=150,
            )
            full_text = text
            yield text

        action = self._clean_json(full_text)
        return Thought(full_text, action)

    def _think_server(self, image_bytes: bytes, instruction: str) -> Thought:
        """OpenAI-compatible server (vLLM-MLX, MLX-OpenAI-Server)."""
        import urllib.request

        messages = [{"role": "system", "content": self.system_prompt}]

        if self.is_vision and image_bytes and len(image_bytes) > 1024:
            b64 = base64.b64encode(image_bytes).decode()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": instruction})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        text = data["choices"][0]["message"]["content"].strip()
        action = self._clean_json(text)
        return Thought(text, action)

    def __del__(self):
        """Release MLX model memory on garbage collection."""
        if self._mlx_model is not None:
            try:
                del self._mlx_model
                self._mlx_model = None
            except Exception:
                pass
        if self._mlx_tokenizer is not None:
            try:
                del self._mlx_tokenizer
                self._mlx_tokenizer = None
            except Exception:
                pass
