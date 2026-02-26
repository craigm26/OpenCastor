"""Setup catalog for provider/model/stack metadata shared by CLI and web setup flows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ProviderSpec:
    """Provider metadata used across setup/auth/conformance UIs."""

    key: str
    label: str
    desc: str
    env_var: Optional[str] = None
    has_oauth: bool = False
    has_cli_login: bool = False
    base_url: Optional[str] = None
    openai_compat: bool = False
    local: bool = False
    setup_visible: bool = True


@dataclass(frozen=True)
class ModelProfile:
    """Model profile metadata for setup menus."""

    id: str
    provider: str
    model: str
    label: str
    desc: str
    tags: tuple[str, ...] = ()
    recommended: bool = False
    apple_use_case: Optional[str] = None
    apple_guardrails: Optional[str] = None


@dataclass(frozen=True)
class StackProfile:
    """Curated stack choices for first-run setup."""

    id: str
    label: str
    desc: str
    provider: str
    model_profile_id: str
    local: bool
    compatibility: tuple[str, ...] = ()
    fallback_stack_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardwarePreset:
    """Hardware preset shown in setup."""

    id: str
    label: str


_PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic (Claude)",
        desc="Best reasoning & safety",
        env_var="ANTHROPIC_API_KEY",
        has_oauth=True,
    ),
    "google": ProviderSpec(
        key="google",
        label="Google (Gemini)",
        desc="Fast, multimodal, robotics",
        env_var="GOOGLE_API_KEY",
        has_oauth=True,
    ),
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI (GPT)",
        desc="Widely supported",
        env_var="OPENAI_API_KEY",
    ),
    "huggingface": ProviderSpec(
        key="huggingface",
        label="Hugging Face",
        desc="Open-source models",
        env_var="HF_TOKEN",
        has_cli_login=True,
    ),
    "ollama": ProviderSpec(
        key="ollama",
        label="Ollama (Local)",
        desc="Free, private, no API needed",
        env_var=None,
        local=True,
    ),
    "llamacpp": ProviderSpec(
        key="llamacpp",
        label="llama.cpp (Local)",
        desc="Bare-metal GGUF inference",
        env_var=None,
        local=True,
    ),
    "mlx": ProviderSpec(
        key="mlx",
        label="MLX (Apple Silicon)",
        desc="Native GPU, 400+ tok/s on Mac",
        env_var=None,
        local=True,
    ),
    "apple": ProviderSpec(
        key="apple",
        label="Apple Foundation Models",
        desc="On-device Apple Intelligence model",
        env_var=None,
        local=True,
    ),
    "groq": ProviderSpec(
        key="groq",
        label="Groq (Ultra-Fast)",
        desc="Sub-second LLM inference",
        env_var="GROQ_API_KEY",
    ),
    "openrouter": ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        desc="Access many models with one key",
        env_var="OPENROUTER_API_KEY",
        setup_visible=False,
    ),
    "moonshot": ProviderSpec(
        key="moonshot",
        label="Kimi / Moonshot AI (CN)",
        desc="Chinese & English",
        env_var="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.cn/v1",
        openai_compat=True,
    ),
    "minimax": ProviderSpec(
        key="minimax",
        label="MiniMax M2.5 (CN)",
        desc="Chinese & English",
        env_var="MINIMAX_API_KEY",
        base_url="https://api.minimax.chat/v1",
        openai_compat=True,
    ),
}

_PROVIDER_ORDER: List[str] = [
    "anthropic",
    "google",
    "openai",
    "huggingface",
    "ollama",
    "llamacpp",
    "mlx",
    "apple",
    "groq",
    "moonshot",
    "minimax",
]

_MODELS: Dict[str, List[Dict[str, Any]]] = {
    "anthropic": [
        {
            "id": "claude-opus-4-6",
            "label": "Claude Opus 4.6",
            "desc": "Best reasoning",
            "tags": ["reasoning", "safety"],
            "recommended": True,
        },
        {
            "id": "claude-sonnet-4-5-20250929",
            "label": "Claude Sonnet 4.5",
            "desc": "Fast, great balance",
            "tags": ["balanced"],
        },
        {
            "id": "claude-haiku-3-5-20241022",
            "label": "Claude Haiku 3.5",
            "desc": "Fastest, most affordable",
            "tags": ["fast"],
        },
    ],
    "google": [
        {
            "id": "gemini-2.5-flash",
            "label": "Gemini 2.5 Flash",
            "desc": "Fast & multimodal",
            "tags": ["fast", "multimodal"],
            "recommended": True,
        },
        {
            "id": "gemini-2.5-pro",
            "label": "Gemini 2.5 Pro",
            "desc": "Deep reasoning",
            "tags": ["reasoning"],
        },
        {
            "id": "gemini-3-flash-preview",
            "label": "Gemini 3 Flash - Agentic Vision (Preview)",
            "desc": "Think-act-observe loop for fine-grained vision tasks",
            "tags": ["preview", "agentic", "vision", "code-execution"],
        },
    ],
    "openai": [
        {
            "id": "gpt-4.1",
            "label": "GPT-4.1",
            "desc": "Latest, most capable",
            "tags": ["reasoning"],
            "recommended": True,
        },
        {
            "id": "gpt-4.1-mini",
            "label": "GPT-4.1 Mini",
            "desc": "Fast & affordable",
            "tags": ["fast"],
        },
        {
            "id": "gpt-4o",
            "label": "GPT-4o",
            "desc": "Vision & tool use",
            "tags": ["multimodal"],
        },
    ],
    "huggingface": [
        {
            "id": "meta-llama/Llama-3.3-70B-Instruct",
            "label": "Llama 3.3 70B",
            "desc": "Best open-source",
            "tags": ["open-source"],
            "recommended": True,
        },
        {
            "id": "Qwen/Qwen2.5-72B-Instruct",
            "label": "Qwen 2.5 72B",
            "desc": "Strong multilingual",
            "tags": ["multilingual"],
        },
        {
            "id": "mistralai/Mistral-Large-Instruct-2407",
            "label": "Mistral Large",
            "desc": "European, fast",
            "tags": ["fast"],
        },
    ],
    "ollama": [],
    "llamacpp": [],
    "mlx": [
        {
            "id": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
            "label": "Qwen 2.5 VL 7B (4-bit)",
            "desc": "Vision + language, recommended",
            "tags": ["vision", "recommended"],
            "recommended": True,
        },
        {
            "id": "mlx-community/Llama-3.3-8B-Instruct-4bit",
            "label": "Llama 3.3 8B (4-bit)",
            "desc": "Fast general purpose",
            "tags": ["fast"],
        },
        {
            "id": "mlx-community/Mistral-Small-3.1-24B-Instruct-2503-4bit",
            "label": "Mistral Small 3.1 24B (4-bit)",
            "desc": "Strong reasoning",
            "tags": ["reasoning"],
        },
    ],
    "apple": [
        {
            "id": "apple-balanced",
            "label": "Apple Balanced",
            "desc": "General model with default guardrails",
            "tags": ["on-device", "recommended"],
            "recommended": True,
            "apple_use_case": "GENERAL",
            "apple_guardrails": "DEFAULT",
        },
        {
            "id": "apple-creative",
            "label": "Apple Creative",
            "desc": "General model with permissive transformations",
            "tags": ["on-device", "creative"],
            "apple_use_case": "GENERAL",
            "apple_guardrails": "PERMISSIVE_CONTENT_TRANSFORMATIONS",
        },
        {
            "id": "apple-tagging",
            "label": "Apple Tagging",
            "desc": "Content tagging use-case with default guardrails",
            "tags": ["on-device", "classification"],
            "apple_use_case": "CONTENT_TAGGING",
            "apple_guardrails": "DEFAULT",
        },
    ],
    "groq": [
        {
            "id": "llama-3.3-70b-versatile",
            "label": "Llama 3.3 70B Versatile",
            "desc": "Best quality, ultra-fast Groq inference",
            "tags": ["fast", "reasoning"],
            "recommended": True,
        },
        {
            "id": "llama-3.1-8b-instant",
            "label": "Llama 3.1 8B Instant",
            "desc": "Smallest, fastest, lowest cost",
            "tags": ["fast", "lightweight"],
        },
        {
            "id": "gemma2-9b-it",
            "label": "Gemma 2 9B",
            "desc": "Google Gemma on Groq hardware",
            "tags": ["fast", "google"],
        },
        {
            "id": "mixtral-8x7b-32768",
            "label": "Mixtral 8x7B",
            "desc": "MoE model, 32K context",
            "tags": ["fast", "long-context"],
        },
    ],
    "moonshot": [
        {
            "id": "moonshot-v1-8k",
            "label": "Kimi k2.5 (8K context)",
            "desc": "Fast, bilingual Chinese/English",
            "tags": ["fast", "bilingual"],
            "recommended": True,
        },
        {
            "id": "moonshot-v1-32k",
            "label": "Kimi k2.5 (32K context)",
            "desc": "Longer context, bilingual",
            "tags": ["long-context", "bilingual"],
        },
    ],
    "minimax": [
        {
            "id": "MiniMax-Text-01",
            "label": "MiniMax M2.5 Text",
            "desc": "Strong Chinese & English reasoning",
            "tags": ["reasoning", "bilingual"],
            "recommended": True,
        },
        {
            "id": "abab6.5s-chat",
            "label": "MiniMax ABAB 6.5s",
            "desc": "Fast, cost-effective",
            "tags": ["fast", "bilingual"],
        },
    ],
}

_SECONDARY_MODELS: List[Dict[str, Any]] = [
    {
        "provider": "google",
        "id": "gemini-er-1.5",
        "label": "Google Gemini Robotics ER 1.5",
        "desc": "Physical AI for robot control",
        "tags": ["robotics", "physical-ai"],
    },
    {
        "provider": "google",
        "id": "gemini-2.5-flash",
        "label": "Google Gemini 2.5 Flash",
        "desc": "Fast vision & multimodal",
        "tags": ["vision", "multimodal"],
    },
    {
        "provider": "openai",
        "id": "gpt-4o",
        "label": "OpenAI GPT-4o",
        "desc": "Vision & tool use",
        "tags": ["vision", "multimodal"],
    },
]

_HARDWARE_PRESETS: List[HardwarePreset] = [
    HardwarePreset(id="rpi_rc_car", label="RPi RC Car + PCA9685 + CSI Camera"),
    HardwarePreset(id="waveshare_alpha", label="Waveshare AlphaBot"),
    HardwarePreset(id="adeept_generic", label="Adeept RaspTank"),
    HardwarePreset(id="freenove_4wd", label="Freenove 4WD Car"),
    HardwarePreset(id="sunfounder_picar", label="SunFounder PiCar-X"),
    HardwarePreset(id="esp32_generic", label="ESP32 Generic Wi-Fi Bot"),
    HardwarePreset(id="lego_mindstorms_ev3", label="LEGO Mindstorms EV3"),
    HardwarePreset(id="lego_spike_prime", label="LEGO SPIKE Prime"),
    HardwarePreset(id="dynamixel_arm", label="Dynamixel Arm"),
]

_STACK_PROFILES: List[StackProfile] = [
    StackProfile(
        id="apple_native",
        label="Apple Native (Recommended on eligible Mac)",
        desc="On-device Apple Foundation Models with profile presets",
        provider="apple",
        model_profile_id="apple-balanced",
        local=True,
        compatibility=("macos", "arm64"),
        fallback_stack_ids=("mlx_local_vision", "ollama_universal_local"),
    ),
    StackProfile(
        id="mlx_local_vision",
        label="MLX Local Vision",
        desc="Apple Silicon local inference with MLX vision model",
        provider="mlx",
        model_profile_id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        local=True,
        compatibility=("macos", "arm64"),
        fallback_stack_ids=("ollama_universal_local",),
    ),
    StackProfile(
        id="ollama_universal_local",
        label="Ollama Universal Local",
        desc="Cross-platform local stack with Ollama",
        provider="ollama",
        model_profile_id="llava:13b",
        local=True,
        fallback_stack_ids=(),
    ),
]


def get_provider_specs(include_hidden: bool = False) -> Dict[str, ProviderSpec]:
    """Return provider specs keyed by provider name."""
    if include_hidden:
        return dict(_PROVIDER_SPECS)
    return {k: v for k, v in _PROVIDER_SPECS.items() if v.setup_visible}


def get_provider_order() -> List[str]:
    """Return stable provider menu order for setup."""
    return list(_PROVIDER_ORDER)


def get_provider_auth_map() -> Dict[str, Dict[str, Any]]:
    """Return legacy auth metadata shape used by wizard/auth flows."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, spec in _PROVIDER_SPECS.items():
        if not spec.setup_visible:
            continue
        entry: Dict[str, Any] = {
            "env_var": spec.env_var,
            "label": spec.label,
            "desc": spec.desc,
        }
        if spec.has_oauth:
            entry["has_oauth"] = True
        if spec.has_cli_login:
            entry["has_cli_login"] = True
        if spec.base_url:
            entry["base_url"] = spec.base_url
        if spec.openai_compat:
            entry["openai_compat"] = True
        out[key] = entry
    return out


def get_provider_models() -> Dict[str, List[Dict[str, Any]]]:
    """Return setup model menu data keyed by provider."""
    return {k: [dict(item) for item in v] for k, v in _MODELS.items()}


def get_secondary_models() -> List[Dict[str, Any]]:
    """Return curated secondary-model options."""
    return [dict(item) for item in _SECONDARY_MODELS]


def get_hardware_presets() -> List[HardwarePreset]:
    """Return available hardware presets for setup."""
    return list(_HARDWARE_PRESETS)


def get_hardware_preset_map() -> Dict[str, Optional[str]]:
    """Return legacy numeric preset map used by CLI wizard."""
    return {
        "1": None,
        "2": "rpi_rc_car",
        "3": "waveshare_alpha",
        "4": "adeept_generic",
        "5": "freenove_4wd",
        "6": "sunfounder_picar",
        "7": "esp32_generic",
        "8": "lego_mindstorms_ev3",
        "9": "lego_spike_prime",
        "10": "dynamixel_arm",
    }


def get_model_profiles(provider: str) -> List[ModelProfile]:
    """Return typed model profiles for a provider."""
    items = _MODELS.get(provider, [])
    profiles: List[ModelProfile] = []
    for item in items:
        profiles.append(
            ModelProfile(
                id=item["id"],
                provider=provider,
                model=item["id"],
                label=item["label"],
                desc=item.get("desc", ""),
                tags=tuple(item.get("tags", [])),
                recommended=bool(item.get("recommended", False)),
                apple_use_case=item.get("apple_use_case"),
                apple_guardrails=item.get("apple_guardrails"),
            )
        )
    return profiles


def get_stack_profiles(device_info: Optional[Dict[str, Any]] = None) -> List[StackProfile]:
    """Return curated stack profiles filtered by compatibility for a device."""
    if not device_info:
        return list(_STACK_PROFILES)

    platform_name = str(device_info.get("platform", "")).lower()
    architecture = str(device_info.get("architecture", "")).lower()

    filtered: List[StackProfile] = []
    for stack in _STACK_PROFILES:
        if not stack.compatibility:
            filtered.append(stack)
            continue

        compat = set(stack.compatibility)
        if "macos" in compat and platform_name != "macos":
            continue
        if "arm64" in compat and architecture not in {"arm64", "aarch64"}:
            continue
        filtered.append(stack)
    return filtered


def get_known_provider_names() -> set[str]:
    """Return known provider names and common aliases for validation layers."""
    names = {
        "anthropic",
        "google",
        "openai",
        "huggingface",
        "ollama",
        "llamacpp",
        "llama.cpp",
        "llama-cpp",
        "mlx",
        "mlx-lm",
        "vllm-mlx",
        "claude_oauth",
        "openrouter",
        "groq",
        "vertex_ai",
        "vertex",
        "vertexai",
        "onnx",
        "onnxruntime",
        "vla",
        "openvla",
        "moonshot",
        "minimax",
        "apple",
        "apple-fm",
        "foundationmodels",
    }
    return names


def get_provider_env_var_map() -> Dict[str, Optional[str]]:
    """Return map of provider -> auth env var (or None when no key needed)."""
    out: Dict[str, Optional[str]] = {}
    for key, spec in _PROVIDER_SPECS.items():
        out[key] = spec.env_var
    out.setdefault("apple-fm", None)
    out.setdefault("foundationmodels", None)
    return out


def iter_setup_visible_providers() -> Iterable[ProviderSpec]:
    """Yield setup-visible providers in menu order."""
    specs = get_provider_specs(include_hidden=False)
    for key in _PROVIDER_ORDER:
        if key in specs:
            yield specs[key]


def get_catalog_schema_info() -> Dict[str, str]:
    """Return setup catalog schema version and stable content hash."""
    payload = {
        "providers": [
            {
                "key": spec.key,
                "label": spec.label,
                "desc": spec.desc,
                "env_var": spec.env_var,
                "local": spec.local,
                "setup_visible": spec.setup_visible,
            }
            for spec in _PROVIDER_SPECS.values()
        ],
        "provider_order": _PROVIDER_ORDER,
        "models": _MODELS,
        "secondary_models": _SECONDARY_MODELS,
        "hardware_presets": [vars(item) for item in _HARDWARE_PRESETS],
        "stack_profiles": [vars(item) for item in _STACK_PROFILES],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return {
        "catalog_version": "setup-catalog-v3",
        "catalog_hash": digest,
    }
