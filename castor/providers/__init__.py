from .anthropic_provider import AnthropicProvider
from .apple_provider import AppleProvider
from .consensus_provider import ConsensusProvider
from .deepseek_provider import DeepSeekProvider
from .embedding_backend import EmbeddingBackend
from .gated import GatedModelProvider
from .google_provider import GoogleProvider
from .grok_provider import GrokProvider
from .groq_provider import GroqProvider
from .huggingface_provider import HuggingFaceProvider
from .llamacpp_provider import LlamaCppProvider
from .mistral_provider import MistralProvider
from .mlx_provider import MLXProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

__all__ = [
    "get_provider",
    "AnthropicProvider",
    "ConsensusProvider",
    "AppleProvider",
    "DeepSeekProvider",
    "EmbeddingBackend",
    "GoogleProvider",
    "GrokProvider",
    "GroqProvider",
    "HuggingFaceProvider",
    "LlamaCppProvider",
    "MistralProvider",
    "MLXProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "GatedModelProvider",
    "OpenRouterProvider",
    "VertexAIProvider",
    "VLAProvider",
]


def _builtin_get_provider(config: dict):
    """Built-in factory: initialise the correct AI provider from *config*.

    Uses module-level class names so that test patches on
    ``castor.providers.<ClassName>`` continue to work correctly.
    """
    provider_name = config.get("provider", "google").lower()

    if provider_name == "google":
        return GoogleProvider(config)
    elif provider_name in ("apple", "apple-fm", "foundationmodels"):
        return AppleProvider(config)
    elif provider_name == "openai":
        return OpenAIProvider(config)
    elif provider_name == "anthropic":
        return AnthropicProvider(config)
    elif provider_name in ("huggingface", "hf"):
        return HuggingFaceProvider(config)
    elif provider_name == "ollama":
        return OllamaProvider(config)
    elif provider_name in ("llamacpp", "llama.cpp", "llama-cpp"):
        return LlamaCppProvider(config)
    elif provider_name in ("mlx", "mlx-lm", "vllm-mlx"):
        return MLXProvider(config)
    elif provider_name in ("vertex_ai", "vertex", "vertexai"):
        from .vertex_provider import VertexAIProvider

        return VertexAIProvider(config)
    elif provider_name in ("onnx", "onnxruntime"):
        from .onnx_provider import ONNXProvider

        return ONNXProvider(config)
    elif provider_name == "groq":
        return GroqProvider(config)
    elif provider_name in ("vla", "openvla"):
        from .vla_provider import VLAProvider

        return VLAProvider(config)
    elif provider_name in ("openrouter", "open_router"):
        from .openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(config)
    elif provider_name in ("deepseek", "deep_seek"):
        return DeepSeekProvider(config)
    elif provider_name in ("grok", "xai"):
        return GrokProvider(config)
    elif provider_name in ("mistral", "mistral_ai", "mistralai"):
        return MistralProvider(config)
    elif provider_name == "consensus":
        return ConsensusProvider(config)
    elif provider_name in ("pool", "provider_pool"):
        from .pool_provider import ProviderPool

        return ProviderPool(config)
    else:
        raise ValueError(f"Unknown AI provider: {provider_name}")


def get_provider(config: dict):
    """Factory function to initialise the correct AI provider.

    Thin wrapper around :meth:`~castor.registry.ComponentRegistry.get_provider`
    that preserves backward compatibility.  Plugin-registered providers take
    precedence; built-in implementations fall back to :func:`_builtin_get_provider`.
    """
    from castor.registry import get_registry

    return get_registry().get_provider(config)
