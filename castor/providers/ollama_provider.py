"""Ollama local LLM provider for OpenCastor.

Connects to a locally-running Ollama instance via its OpenAI-compatible
API (default: ``http://localhost:11434``).  Supports text generation,
vision (multimodal models like ``llava``), and streaming.

No API key is required — Ollama runs entirely on your machine.

Environment variables:
    OLLAMA_HOST  — Override the default base URL (e.g. ``http://192.168.1.50:11434``)

Features:
    - Graceful degradation when Ollama is unavailable
    - Model list caching with configurable TTL
    - Auto-pull models on first use
    - Model aliases (e.g. "vision" → "llava:latest")
    - Connection profiles for remote Ollama servers
    - Configurable timeouts for generation vs health checks
"""

import base64
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Ollama")

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llava:13b"
DEFAULT_GENERATION_TIMEOUT = 30
DEFAULT_HEALTH_TIMEOUT = 5
DEFAULT_MODEL_CACHE_TTL = 300  # 5 minutes

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

# Built-in model aliases — users can override via config
DEFAULT_MODEL_ALIASES: Dict[str, str] = {
    "vision": "llava:latest",
    "fast": "llama3.2:1b",
    "code": "codellama:latest",
    "chat": "llama3:latest",
    "small": "llama3.2:1b",
    "large": "llama3:70b",
}

# Connection profiles stored in config under "ollama_profiles"
# Format: {"homeserver": {"host": "http://192.168.1.50:11434"}, ...}


class OllamaConnectionError(ConnectionError):
    """Raised when the Ollama server is unreachable."""

    def __init__(self, host: str, original: Optional[Exception] = None):
        self.host = host
        self.original = original
        hint = _connection_hint(host, original)
        super().__init__(hint)


class OllamaModelNotFoundError(ValueError):
    """Raised when a requested model is not available locally."""

    def __init__(self, model: str, available: Optional[List[str]] = None):
        self.model = model
        self.available = available or []
        avail_str = ", ".join(self.available[:5]) if self.available else "none"
        super().__init__(
            f"Model '{model}' is not available locally. "
            f"Available models: {avail_str}. "
            f"Pull it with: ollama pull {model}"
        )


def _connection_hint(host: str, original: Optional[Exception] = None) -> str:
    """Build a genuinely helpful error message for connection failures."""
    is_localhost = any(h in host for h in ("localhost", "127.0.0.1", "0.0.0.0"))

    if isinstance(original, ConnectionRefusedError):
        if is_localhost:
            return (
                f"Cannot connect to Ollama at {host} — connection refused.\n"
                f"Ollama doesn't appear to be running. Start it with:\n"
                f"  ollama serve\n"
                f"Or install it from: https://ollama.com/download"
            )
        return (
            f"Cannot connect to Ollama at {host} — connection refused.\n"
            f"The remote Ollama server may be down or not accepting connections.\n"
            f"Check that the server is running and the port is open."
        )

    if isinstance(original, TimeoutError) or (
        isinstance(original, OSError) and "timed out" in str(original).lower()
    ):
        return (
            f"Connection to Ollama at {host} timed out.\n"
            f"The server may be overloaded or unreachable.\n"
            f"Check your network connection"
            + (" and firewall settings." if not is_localhost else ".")
        )

    if isinstance(original, URLError):
        reason = str(getattr(original, "reason", original))
        if "name or service not known" in reason.lower():
            return (
                f"Cannot resolve host in '{host}'.\n"
                f"Check the hostname/IP address. "
                f"Set OLLAMA_HOST env var or use 'castor login ollama --host <host>'."
            )

    return f"Cannot connect to Ollama at {host}. Is Ollama running? Start it with: ollama serve"


def _resolve_host(config: Dict[str, Any], profile: Optional[str] = None) -> str:
    """Resolve the Ollama host URL from env, config, or named profile."""
    # Named profile takes precedence if specified
    if profile:
        profiles = config.get("ollama_profiles", {})
        if profile in profiles:
            return profiles[profile].get("host", DEFAULT_HOST).rstrip("/")
        logger.warning(
            "Ollama profile '%s' not found. Available: %s",
            profile,
            list(profiles.keys()) or "none",
        )

    host = (
        os.getenv("OLLAMA_HOST")
        or config.get("ollama_host")
        or config.get("endpoint_url")
        or DEFAULT_HOST
    )
    return host.rstrip("/")


def _resolve_model_alias(model: str, aliases: Dict[str, str]) -> str:
    """Resolve a model alias to its full name."""
    return aliases.get(model, model)


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
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Ollama returned malformed JSON (%d bytes): %s",
                len(raw),
                raw[:200],
            )
            # Try to salvage partial JSON
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
            raise ValueError(
                f"Ollama returned invalid JSON. "
                f"This may indicate the model is still loading or the server is overloaded. "
                f"Response preview: {raw[:100]!r}"
            ) from exc

    except (URLError, OSError, ConnectionRefusedError) as exc:
        host = "/".join(url.split("/")[:3])
        raise OllamaConnectionError(host, exc) from exc


class _ModelCache:
    """Simple TTL cache for model list."""

    def __init__(self, ttl: float = DEFAULT_MODEL_CACHE_TTL):
        self.ttl = ttl
        self._models: Optional[List[Dict[str, Any]]] = None
        self._fetched_at: float = 0.0

    @property
    def expired(self) -> bool:
        return self._models is None or (time.time() - self._fetched_at) > self.ttl

    def get(self) -> Optional[List[Dict[str, Any]]]:
        if self.expired:
            return None
        return self._models

    def set(self, models: List[Dict[str, Any]]) -> None:
        self._models = models
        self._fetched_at = time.time()

    def invalidate(self) -> None:
        self._models = None
        self._fetched_at = 0.0

    def model_names(self) -> List[str]:
        if self._models is None:
            return []
        return [m["name"] for m in self._models]


class OllamaProvider(BaseProvider):
    """Ollama local LLM adapter.

    Works with any model pulled into Ollama.  For vision-capable models
    (LLaVA, BakLLaVA, Moondream, etc.) images are sent as base64 payloads.

    Uses Ollama's ``/api/chat`` endpoint for chat completions and
    ``/api/tags`` for model listing.

    Config options:
        - ``timeout``: Generation timeout in seconds (default: 30)
        - ``health_timeout``: Health check timeout in seconds (default: 5)
        - ``model_cache_ttl``: Model list cache TTL in seconds (default: 300)
        - ``auto_pull``: Auto-pull missing models (default: False)
        - ``model_aliases``: Dict of alias → model name overrides
        - ``ollama_host``: Ollama server URL
        - ``ollama_profile``: Named connection profile to use
        - ``ollama_profiles``: Dict of profile_name → {host: url}
        - ``vision_enabled``: Force vision mode (default: auto-detect)
        - ``system_prompt``: Custom system prompt (overrides default)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Connection profile
        profile = config.get("ollama_profile")
        self.host = _resolve_host(config, profile=profile)

        # Model aliases
        self._aliases = {**DEFAULT_MODEL_ALIASES}
        self._aliases.update(config.get("model_aliases", {}))

        # Resolve alias for model name
        if self.model_name == "default-model":
            self.model_name = DEFAULT_MODEL
        else:
            self.model_name = _resolve_model_alias(self.model_name, self._aliases)

        self.is_vision = _is_vision_model(self.model_name) or config.get("vision_enabled", False)

        # Timeouts
        self.timeout = config.get("timeout", DEFAULT_GENERATION_TIMEOUT)
        self.health_timeout = config.get("health_timeout", DEFAULT_HEALTH_TIMEOUT)

        # Auto-pull
        self.auto_pull = config.get("auto_pull", False)
        self._pull_progress_callback: Optional[Callable[[str, float], None]] = None

        # Model cache
        self._model_cache = _ModelCache(ttl=config.get("model_cache_ttl", DEFAULT_MODEL_CACHE_TTL))

        # Custom system prompt
        custom_prompt = config.get("system_prompt")
        if custom_prompt:
            self.system_prompt = custom_prompt

        # Availability flag
        self._available: Optional[bool] = None

        # Verify connectivity (non-fatal warning)
        try:
            self._ping()
            self._available = True
            logger.info(
                "Ollama provider ready — host=%s model=%s vision=%s",
                self.host,
                self.model_name,
                self.is_vision,
            )
        except OllamaConnectionError:
            self._available = False
            logger.warning(
                "Ollama is not reachable at %s. "
                "Requests will fail until Ollama is started: ollama serve",
                self.host,
            )

    @property
    def is_available(self) -> bool:
        """Whether Ollama was reachable at init time."""
        return self._available is True

    def set_pull_progress_callback(self, callback: Callable[[str, float], None]) -> None:
        """Set a callback for model pull progress.

        Args:
            callback: Function(status_text, fraction_complete) called during pulls.
                      fraction_complete is 0.0–1.0, or -1 if unknown.
        """
        self._pull_progress_callback = callback

    def resolve_alias(self, alias: str) -> str:
        """Resolve a model alias to its full name.

        Args:
            alias: Alias or model name.

        Returns:
            Resolved model name.
        """
        return _resolve_model_alias(alias, self._aliases)

    def _ping(self) -> bool:
        """Check if Ollama is running.

        Returns:
            True if Ollama responds.

        Raises:
            OllamaConnectionError: If the server is unreachable.
        """
        _http_request(f"{self.host}/", timeout=self.health_timeout)
        return True

    def _ensure_model_available(self, model: str) -> None:
        """Ensure a model is available locally, optionally pulling it.

        Args:
            model: Model name to check.

        Raises:
            OllamaModelNotFoundError: If model is missing and auto_pull is False.
            OllamaConnectionError: If Ollama is unreachable.
        """
        try:
            models = self.list_models()
            names = [m["name"] for m in models]
            # Check both exact match and base name match
            if any(model == n or model == n.split(":")[0] for n in names):
                return
        except OllamaConnectionError:
            raise

        if self.auto_pull:
            logger.info("Model '%s' not found locally, pulling...", model)
            self.pull_model(model)
            self._model_cache.invalidate()
            return

        available = [m["name"] for m in self.list_models()]
        raise OllamaModelNotFoundError(model, available)

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

        Uses a TTL cache to avoid hitting /api/tags every call.

        Returns:
            List of dicts with model info (name, size, modified_at, etc.).

        Raises:
            OllamaConnectionError: If Ollama is not reachable.
        """
        cached = self._model_cache.get()
        if cached is not None:
            return cached

        response = _http_request(f"{self.host}/api/tags", timeout=self.health_timeout)
        raw_models = response.get("models", [])
        models = [
            {
                "name": m.get("name", "unknown"),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
                "digest": m.get("digest", "")[:12],
                "details": m.get("details", {}),
            }
            for m in raw_models
        ]
        self._model_cache.set(models)
        return models

    def pull_model(
        self,
        model_name: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        """Pull a model from the Ollama registry.

        Args:
            model_name: Model to pull (e.g. ``llava:13b``).
            progress_callback: Optional fn(status, fraction) for progress.
                If not provided, uses the instance-level callback.

        Raises:
            OllamaConnectionError: If Ollama is not reachable.
        """
        cb = progress_callback or self._pull_progress_callback

        if cb is None:
            # Non-streaming pull
            _http_request(
                f"{self.host}/api/pull",
                data={"name": model_name, "stream": False},
                timeout=600,
            )
            self._model_cache.invalidate()
            return

        # Streaming pull with progress
        resp = _http_request(
            f"{self.host}/api/pull",
            data={"name": model_name, "stream": True},
            timeout=600,
            stream=True,
        )

        for line in resp:
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8"))
                status = chunk.get("status", "")
                total = chunk.get("total", 0)
                completed = chunk.get("completed", 0)
                fraction = completed / total if total > 0 else -1.0
                cb(status, fraction)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        self._model_cache.invalidate()
