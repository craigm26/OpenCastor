"""Ollama local LLM provider for OpenCastor.

Connects to a locally-running Ollama instance via its OpenAI-compatible
API (default: ``http://localhost:11434``).  Supports text generation,
vision (multimodal models like ``llava``), and streaming.

No API key is required — Ollama runs entirely on your machine.

Environment variables:
    OLLAMA_HOST  — Override the default base URL (e.g. ``http://192.168.1.50:11434``)
"""

import base64
import json
import logging
import os
from typing import Any, Dict, Iterator, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Ollama")

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llava:13b"

# Models known to accept image input
VISION_MODELS = {
    "llava",
    "llava:7b",
    "llava:13b",
    "llava:34b",
    "llava-llama3",
    "llava-phi3",
    "bakllava",
    "moondream",
    "minicpm-v",
}


class OllamaConnectionError(ConnectionError):
    """Raised when the Ollama server is unreachable."""

    def __init__(self, host: str, original: Optional[Exception] = None):
        self.host = host
        self.original = original
        super().__init__(
            f"Cannot connect to Ollama at {host}. Is Ollama running? Start it with: ollama serve"
        )


def _resolve_host(config: Dict[str, Any]) -> str:
    """Resolve the Ollama host URL from env or config."""
    host = (
        os.getenv("OLLAMA_HOST")
        or config.get("ollama_host")
        or config.get("endpoint_url")
        or DEFAULT_HOST
    )
    return host.rstrip("/")


def _is_vision_model(model_name: str) -> bool:
    """Check if a model is known to support vision input."""
    base = model_name.split(":")[0].lower()
    return base in VISION_MODELS or model_name.lower() in VISION_MODELS


def _http_request(
    url: str,
    data: Optional[dict] = None,
    timeout: int = 120,
    stream: bool = False,
) -> Any:
    """Make an HTTP request to the Ollama API.

    Args:
        url: Full URL to request.
        data: JSON body (POST if provided, GET otherwise).
        timeout: Request timeout in seconds.
        stream: If True, return the raw response for streaming.

    Returns:
        Parsed JSON response, or raw response object if streaming.

    Raises:
        OllamaConnectionError: If the server is unreachable.
    """
    try:
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            req = Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
        else:
            req = Request(url)

        resp = urlopen(req, timeout=timeout)

        if stream:
            return resp

        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    except (URLError, OSError, ConnectionRefusedError) as exc:
        # Extract host from URL for the error message
        host = "/".join(url.split("/")[:3])
        raise OllamaConnectionError(host, exc) from exc


class OllamaProvider(BaseProvider):
    """Ollama local LLM adapter.

    Works with any model pulled into Ollama.  For vision-capable models
    (LLaVA, BakLLaVA, Moondream, etc.) images are sent as base64 payloads.

    Uses Ollama's ``/api/chat`` endpoint for chat completions and
    ``/api/tags`` for model listing.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.host = _resolve_host(config)

        if self.model_name == "default-model":
            self.model_name = DEFAULT_MODEL

        self.is_vision = _is_vision_model(self.model_name) or config.get("vision_enabled", False)

        self.timeout = config.get("timeout", 120)

        # Verify connectivity (non-fatal warning)
        try:
            self._ping()
            logger.info(
                "Ollama provider ready — host=%s model=%s vision=%s",
                self.host,
                self.model_name,
                self.is_vision,
            )
        except OllamaConnectionError:
            logger.warning(
                "Ollama is not reachable at %s. "
                "Requests will fail until Ollama is started: ollama serve",
                self.host,
            )

    def _ping(self) -> bool:
        """Check if Ollama is running by hitting the root endpoint.

        Returns:
            True if Ollama responds.

        Raises:
            OllamaConnectionError: If the server is unreachable.
        """
        _http_request(f"{self.host}/", timeout=5)
        return True

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        """Generate a response from the Ollama model.

        Args:
            image_bytes: Raw JPEG image bytes (can be empty for text-only).
            instruction: Text instruction/prompt.

        Returns:
            A Thought object with the model's response and parsed action.
        """
        try:
            if self.is_vision and image_bytes:
                return self._think_vision(image_bytes, instruction)
            else:
                return self._think_text(instruction)
        except OllamaConnectionError:
            raise
        except Exception as e:
            logger.error("Ollama inference error: %s", e)
            return Thought(f"Error: {e}", None)

    def _think_vision(self, image_bytes: bytes, instruction: str) -> Thought:
        """Send image + instruction to a vision-language model via /api/chat."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": instruction,
                    "images": [b64_image],
                },
            ],
            "stream": False,
        }

        response = _http_request(
            f"{self.host}/api/chat",
            data=payload,
            timeout=self.timeout,
        )

        text = response.get("message", {}).get("content", "")
        action = self._clean_json(text)
        return Thought(text, action)

    def _think_text(self, instruction: str) -> Thought:
        """Text-only inference via /api/chat."""
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": instruction},
            ],
            "stream": False,
        }

        response = _http_request(
            f"{self.host}/api/chat",
            data=payload,
            timeout=self.timeout,
        )

        text = response.get("message", {}).get("content", "")
        action = self._clean_json(text)
        return Thought(text, action)

    def think_stream(self, image_bytes: bytes, instruction: str) -> Iterator[str]:
        """Stream tokens from the Ollama model.

        Yields individual text chunks as they arrive.

        Args:
            image_bytes: Raw JPEG image bytes (can be empty for text-only).
            instruction: Text instruction/prompt.

        Yields:
            String chunks of the model's response.
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
        ]

        user_msg: Dict[str, Any] = {"role": "user", "content": instruction}
        if self.is_vision and image_bytes:
            b64_image = base64.b64encode(image_bytes).decode("utf-8")
            user_msg["images"] = [b64_image]
        messages.append(user_msg)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
        }

        resp = _http_request(
            f"{self.host}/api/chat",
            data=payload,
            timeout=self.timeout,
            stream=True,
        )

        for line in resp:
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8"))
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

    def list_models(self) -> List[Dict[str, Any]]:
        """List models available in the local Ollama instance.

        Returns:
            List of dicts with model info (name, size, modified_at, etc.).

        Raises:
            OllamaConnectionError: If Ollama is not reachable.
        """
        response = _http_request(f"{self.host}/api/tags", timeout=10)
        models = response.get("models", [])
        return [
            {
                "name": m.get("name", "unknown"),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
                "digest": m.get("digest", "")[:12],
                "details": m.get("details", {}),
            }
            for m in models
        ]

    def pull_model(self, model_name: str) -> None:
        """Pull a model from the Ollama registry.

        Args:
            model_name: Model to pull (e.g. ``llava:13b``).

        Raises:
            OllamaConnectionError: If Ollama is not reachable.
        """
        _http_request(
            f"{self.host}/api/pull",
            data={"name": model_name, "stream": False},
            timeout=600,  # Models can be large
        )
