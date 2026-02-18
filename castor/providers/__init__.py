from .anthropic_provider import AnthropicProvider
from .google_provider import GoogleProvider
from .huggingface_provider import HuggingFaceProvider
from .llamacpp_provider import LlamaCppProvider
from .mlx_provider import MLXProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

__all__ = [
    "get_provider",
    "AnthropicProvider",
    "GoogleProvider",
    "HuggingFaceProvider",
    "LlamaCppProvider",
    "MLXProvider",
    "OllamaProvider",
    "OpenAIProvider",
]


def get_provider(config: dict):
    """
    Factory function to initialize the correct AI provider.
    Reads the 'provider' key from the RCAN agent config block.
    """
    provider_name = config.get("provider", "google").lower()

    if provider_name == "google":
        return GoogleProvider(config)
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
    else:
        raise ValueError(f"Unknown AI provider: {provider_name}")
