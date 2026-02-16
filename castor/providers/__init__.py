from .google_provider import GoogleProvider
from .openai_provider import OpenAIProvider
from .anthropic_provider import AnthropicProvider


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
    elif provider_name == "ollama":
        raise NotImplementedError("Ollama support coming soon!")
    else:
        raise ValueError(f"Unknown AI provider: {provider_name}")
