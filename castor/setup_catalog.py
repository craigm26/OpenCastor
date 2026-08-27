"""Setup catalog for provider/model/stack metadata shared by CLI and web setup flows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional


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


_PROVIDER_SPECS: dict[str, ProviderSpec] = {
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

_PROVIDER_ORDER: list[str] = [
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

_MODELS: dict[str, list[dict[str, Any]]] = {
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
            "desc": "Fastest production Gemini — multimodal, low latency, great for real-time robot control",
            "tags": ["fast", "multimodal", "production"],
            "recommended": True,
        },
        {
            "id": "gemini-2.5-pro",
            "label": "Gemini 2.5 Pro",
            "desc": "Most capable production Gemini — deep reasoning and complex scene understanding",
            "tags": ["reasoning", "multimodal", "production"],
        },
        {
            "id": "gemini-2.5-flash-8b",
            "label": "Gemini 2.5 Flash 8B",
            "desc": "Ultra-fast 8B Gemini — minimal latency for reactive robot loops",
            "tags": ["fast", "multimodal", "edge"],
        },
        {
            "id": "gemini-2.5-flash-preview",
            "label": "Gemini 2.5 Flash (Preview)",
            "desc": "Preview channel for Gemini 2.5 Flash with latest improvements",
            "tags": ["fast", "multimodal", "preview"],
        },
        {
            "id": "gemini-2.5-pro-preview",
            "label": "Gemini 2.5 Pro (Preview)",
            "desc": "Preview channel for Gemini 2.5 Pro with latest improvements",
            "tags": ["reasoning", "multimodal", "preview"],
        },
        {
            "id": "gemini-3-flash-preview",
            "label": "Gemini 3 Flash — Agentic Vision (Preview)",
            "desc": "Think→Act→Observe loop for fine-grained vision tasks via code execution",
            "tags": ["preview", "agentic", "vision", "code-execution"],
        },
        {
            "id": "gemini-3.1-pro",
            "label": "Gemini 3.1 Pro (Preview)",
            "desc": "Next-generation top-tier reasoning & multimodal (preview)",
            "tags": ["reasoning", "multimodal", "preview"],
        },
        {
            "id": "gemini-3.1-flash",
            "label": "Gemini 3.1 Flash (Preview)",
            "desc": "Next-generation fast multimodal with strong tool use (preview)",
            "tags": ["fast", "multimodal", "tool-use", "preview"],
        },
        {
            "id": "gemini-er-1.6",
            "label": "Gemini Robotics ER 1.6",
            "desc": "Improved embodied AI with better spatial reasoning and manipulation planning",
            "tags": ["robotics", "physical-ai", "manipulation"],
        },
        {
            "id": "gemini-er-1.5",
            "label": "Gemini Robotics ER 1.5",
            "desc": "Robotics-focused model for embodied tasks",
            "tags": ["robotics", "physical-ai"],
        },
        {
            "id": "gemma-3-27b-it",
            "label": "Gemma 3 27B Instruct",
            "desc": "High-quality open model (Kaggle/HuggingFace available)",
            "tags": ["gemma", "open-model", "kaggle", "huggingface"],
        },
        {
            "id": "gemma-3-12b-it",
            "label": "Gemma 3 12B Instruct",
            "desc": "Balanced Gemma model for quality and cost",
            "tags": ["gemma", "balanced", "open-model"],
        },
        {
            "id": "gemma-3-4b-it",
            "label": "Gemma 3 4B Instruct",
            "desc": "Smaller Gemma model for quick responses",
            "tags": ["gemma", "fast", "cost-effective"],
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
    "ollama": [
        # ── Vision-Language-Action models (VLA) — Brain 1 reactive layer ──
        {
            "id": "moondream:latest",
            "label": "Moondream 2 (1.8B)",
            "desc": "Tiny vision model — runs on Pi 5 / Hailo NPU, <200 ms latency. Best for reactive loop.",
            "tags": ["vision", "vla", "edge", "fast", "recommended"],
            "recommended": True,
        },
        {
            "id": "smolvlm:500m",
            "label": "SmolVLM 500M",
            "desc": "500 M parameter vision model — fastest local option, great for Raspberry Pi",
            "tags": ["vision", "vla", "edge", "fast"],
        },
        {
            "id": "smolvlm:2.2b",
            "label": "SmolVLM 2.2B",
            "desc": "2.2 B parameter vision model — better scene understanding than 500M, still Pi-friendly",
            "tags": ["vision", "vla", "edge"],
        },
        {
            "id": "qwen2.5vl:3b",
            "label": "Qwen 2.5 VL 3B",
            "desc": "Alibaba's 3B vision-language model — strong grounding and manipulation planning",
            "tags": ["vision", "vla", "edge", "grounding"],
        },
        {
            "id": "qwen2.5vl:7b",
            "label": "Qwen 2.5 VL 7B",
            "desc": "7B vision-language model — best local quality for scene understanding and pick-and-place",
            "tags": ["vision", "vla", "grounding"],
        },
        {
            "id": "minicpm-v:8b",
            "label": "MiniCPM-V 8B",
            "desc": "Openbmb 8B vision model — strong OCR and spatial reasoning, good for manipulation tasks",
            "tags": ["vision", "vla", "ocr", "spatial"],
        },
        {
            "id": "granite3.2-vision:2b",
            "label": "Granite 3.2 Vision 2B (IBM)",
            "desc": "IBM's 2B edge vision model — designed for on-device deployment and structured output",
            "tags": ["vision", "vla", "edge", "structured-output"],
        },
        {
            "id": "llava:7b",
            "label": "LLaVA 7B",
            "desc": "Established vision-language model — reliable baseline for camera frame analysis",
            "tags": ["vision", "vla", "stable"],
        },
        {
            "id": "llava:13b",
            "label": "LLaVA 13B",
            "desc": "Larger LLaVA — better scene description, requires ~10 GB RAM",
            "tags": ["vision", "vla"],
        },
        {
            "id": "llava-llama3:8b",
            "label": "LLaVA-LLaMA 3 8B",
            "desc": "LLaVA with LLaMA 3 backbone — improved instruction following over base LLaVA",
            "tags": ["vision", "vla", "instruction-following"],
        },
        # ── Text-only fast models (useful as fast/planner without vision) ──
        {
            "id": "llama3.2:3b",
            "label": "LLaMA 3.2 3B",
            "desc": "Very fast 3B text model — sub-100 ms on Pi 5, good for command parsing without camera",
            "tags": ["fast", "edge", "text-only"],
        },
        {
            "id": "phi4-mini:3.8b",
            "label": "Phi-4 Mini 3.8B (Microsoft)",
            "desc": "Microsoft's 3.8B reasoning model — punches above its weight for planning tasks",
            "tags": ["fast", "reasoning", "edge"],
        },
        {
            "id": "gemma3:4b",
            "label": "Gemma 3 4B",
            "desc": "Google's 4B open model — lightweight, multimodal (text+image), Pi-deployable",
            "tags": ["fast", "edge", "multimodal"],
        },
    ],
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
            "desc": "General chat and robot commands — best starting point",
            "tags": ["on-device", "recommended"],
            "recommended": True,
            "apple_use_case": "GENERAL",
            "apple_guardrails": "DEFAULT",
        },
        {
            "id": "apple-creative",
            "label": "Apple Creative",
            "desc": "Creative tasks, less restrictive output",
            "tags": ["on-device", "creative"],
            "apple_use_case": "GENERAL",
            "apple_guardrails": "PERMISSIVE_CONTENT_TRANSFORMATIONS",
        },
        {
            "id": "apple-tagging",
            "label": "Apple Tagging",
            "desc": "Classifying or labeling objects/scenes",
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

_SECONDARY_MODELS: list[dict[str, Any]] = [
    {
        "provider": "google",
        "id": "gemini-er-1.6",
        "label": "Google Gemini Robotics ER 1.6",
        "desc": "Improved embodied AI with better manipulation planning",
        "tags": ["robotics", "physical-ai", "manipulation"],
    },
    {
        "provider": "google",
        "id": "gemini-er-1.5",
        "label": "Google Gemini Robotics ER 1.5",
        "desc": "Physical AI for robot control",
        "tags": ["robotics", "physical-ai"],
    },
    {
        "provider": "google",
        "id": "gemini-3.1-flash",
        "label": "Google Gemini 3.1 Flash",
        "desc": "Fast vision & multimodal",
        "tags": ["vision", "multimodal", "fast"],
    },
    {
        "provider": "google",
        "id": "gemma-3-12b-it",
        "label": "Google Gemma 3 12B Instruct",
        "desc": "Open model option available on Kaggle/HuggingFace",
        "tags": ["gemma", "open-model", "kaggle", "huggingface"],
    },
    {
        "provider": "openai",
        "id": "gpt-4o",
        "label": "OpenAI GPT-4o",
        "desc": "Vision & tool use",
        "tags": ["vision", "multimodal"],
    },
]

_HARDWARE_PRESETS: list[HardwarePreset] = [
    HardwarePreset(id="rpi_rc_car", label="RPi RC Car + PCA9685 + CSI Camera"),
    HardwarePreset(id="waveshare_alpha", label="Waveshare AlphaBot"),
    HardwarePreset(id="adeept_generic", label="Adeept RaspTank"),
    HardwarePreset(id="freenove_4wd", label="Freenove 4WD Car"),
    HardwarePreset(id="sunfounder_picar", label="SunFounder PiCar-X"),
    HardwarePreset(id="esp32_generic", label="ESP32 Generic Wi-Fi Bot"),
    HardwarePreset(id="lego_mindstorms_ev3", label="LEGO Mindstorms EV3"),
    HardwarePreset(id="lego_spike_prime", label="LEGO SPIKE Prime"),
    HardwarePreset(id="dynamixel_arm", label="Dynamixel Arm"),
    HardwarePreset(id="so_arm101", label="SO-ARM101 (HuggingFace LeRobot, 5-DOF)"),
    HardwarePreset(
        id="so_arm101_bimanual", label="SO-ARM101 Bimanual / ALOHA (leader + follower pair)"
    ),
    HardwarePreset(id="hlabs_acb_single", label="HLabs ACB v2.0 (single BLDC motor)"),
    HardwarePreset(id="hlabs_acb_arm_3dof", label="HLabs ACB v2.0 Arm (3-DOF)"),
    HardwarePreset(id="hlabs_acb_biped_6dof", label="HLabs ACB v2.0 Biped (6-DOF)"),
    HardwarePreset(
        id="pollen_microduck", label="Pollen Microduck (biped duck) — or run `castor duck`"
    ),
]

_STACK_PROFILES: list[StackProfile] = [
    StackProfile(
        id="apple_native",
        label="Apple Native (Recommended on eligible Mac)",
        desc="Mac with Apple Silicon (M1–M4) — runs models on-device via Apple Foundation Models. No API key needed.",
        provider="apple",
        model_profile_id="apple-balanced",
        local=True,
        compatibility=("macos", "arm64"),
        fallback_stack_ids=("mlx_local_vision", "ollama_universal_local"),
    ),
    StackProfile(
        id="mlx_local_vision",
        label="MLX Local Vision",
        desc="Mac with Apple Silicon — open-source models via MLX (Llama, Mistral, Qwen). More model choice than apple_native.",
        provider="mlx",
        model_profile_id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        local=True,
        compatibility=("macos", "arm64"),
        fallback_stack_ids=("ollama_universal_local",),
    ),
    StackProfile(
        id="ollama_universal_local",
        label="Ollama Universal Local",
        desc="Any machine — runs local models via Ollama. Works on Mac, Linux, and Windows.",
        provider="ollama",
        model_profile_id="llava:13b",
        local=True,
        fallback_stack_ids=(),
    ),
]


def get_provider_specs(include_hidden: bool = False) -> dict[str, ProviderSpec]:
    """Return provider specs keyed by provider name."""
    if include_hidden:
        return dict(_PROVIDER_SPECS)
    return {k: v for k, v in _PROVIDER_SPECS.items() if v.setup_visible}


def get_provider_order() -> list[str]:
    """Return stable provider menu order for setup."""
    return list(_PROVIDER_ORDER)


def get_provider_auth_map() -> dict[str, dict[str, Any]]:
    """Return legacy auth metadata shape used by wizard/auth flows."""
    out: dict[str, dict[str, Any]] = {}
    for key, spec in _PROVIDER_SPECS.items():
        if not spec.setup_visible:
            continue
        entry: dict[str, Any] = {
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


def get_provider_models() -> dict[str, list[dict[str, Any]]]:
    """Return setup model menu data keyed by provider."""
    return {k: [dict(item) for item in v] for k, v in _MODELS.items()}


def get_secondary_models() -> list[dict[str, Any]]:
    """Return curated secondary-model options."""
    return [dict(item) for item in _SECONDARY_MODELS]


def get_hardware_presets() -> list[HardwarePreset]:
    """Return available hardware presets for setup."""
    return list(_HARDWARE_PRESETS)


def get_hardware_preset_map() -> dict[str, Optional[str]]:
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
        "11": "so_arm101",
        "12": "so_arm101_bimanual",
        "13": "hlabs_acb_single",
        "14": "hlabs_acb_arm_3dof",
        "15": "hlabs_acb_biped_6dof",
        "16": "pollen_microduck",
    }


def get_model_profiles(provider: str) -> list[ModelProfile]:
    """Return typed model profiles for a provider."""
    items = _MODELS.get(provider, [])
    profiles: list[ModelProfile] = []
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


def get_stack_profiles(device_info: Optional[dict[str, Any]] = None) -> list[StackProfile]:
    """Return curated stack profiles filtered by compatibility for a device."""
    if not device_info:
        return list(_STACK_PROFILES)

    platform_name = str(device_info.get("platform", "")).lower()
    architecture = str(device_info.get("architecture", "")).lower()

    filtered: list[StackProfile] = []
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
        "taalas",
        "taalas-hc1",
    }
    return names


def get_provider_env_var_map() -> dict[str, Optional[str]]:
    """Return map of provider -> auth env var (or None when no key needed)."""
    out: dict[str, Optional[str]] = {}
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


def get_catalog_schema_info() -> dict[str, str]:
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
