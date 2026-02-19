"""
OpenCastor Setup Wizard.
Interactively generates an RCAN-compliant configuration file,
collects API keys, and configures messaging channels.

Features:
  - Safety acknowledgment before physical hardware setup
  - QuickStart (sensible defaults) vs Advanced flow
  - Separate provider selection, authentication, and model choice
  - Secondary model support for vision, robotics, embeddings
  - Inline API key validation
  - Auto-hardware detection
  - Post-wizard health check
  - Rich terminal output (with fallback)
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from castor import __version__

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install pyyaml")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Rich console (optional, graceful fallback)
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn

    _console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    _console = None


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def _print(text: str = "", style: str = None):
    """Print with Rich if available, otherwise plain print."""
    if HAS_RICH and style:
        _console.print(text, style=style)
    elif HAS_RICH:
        _console.print(text)
    else:
        print(text)


BANNER = f"""{Colors.BLUE}
   ___                   ___         _
  / _ \\ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _
 | (_) | '_ \\ / -_) '_ \\ (__/ _` (_-<  _/ _ \\ '_|
  \\___/| .__/ \\___|_| |_|\\___\\__,_/__/\\__\\___/_|
       |_|
{Colors.ENDC}"""

# ---------------------------------------------------------------------------
# Legacy PROVIDERS dict — kept for backward compatibility with tests
# ---------------------------------------------------------------------------
PROVIDERS = {
    "1": {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "label": "Anthropic Claude Opus 4.6",
        "env_var": "ANTHROPIC_API_KEY",
    },
    "2": {
        "provider": "google",
        "model": "gemini-2.5-flash",
        "label": "Google Gemini 2.5 Flash",
        "env_var": "GOOGLE_API_KEY",
    },
    "3": {
        "provider": "google",
        "model": "gemini-3-flash-preview",
        "label": "Google Gemini 3 Flash (Preview)",
        "env_var": "GOOGLE_API_KEY",
    },
    "4": {
        "provider": "openai",
        "model": "gpt-4.1",
        "label": "OpenAI GPT-4.1",
        "env_var": "OPENAI_API_KEY",
    },
    "5": {
        "provider": "huggingface",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "label": "Hugging Face (Llama, Qwen, Mistral, etc.)",
        "env_var": "HF_TOKEN",
    },
    "6": {
        "provider": "ollama",
        "model": "llava:13b",
        "label": "Local Llama (Ollama)",
        "env_var": None,
    },
    "7": {
        "provider": "llamacpp",
        "model": "gemma3:1b",
        "label": "llama.cpp (Local GGUF)",
        "env_var": None,
    },
    "8": {
        "provider": "mlx",
        "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        "label": "MLX (Apple Silicon — 400+ tok/s)",
        "env_var": None,
    },
}

# ---------------------------------------------------------------------------
# New data model: providers separated from models
# ---------------------------------------------------------------------------
PROVIDER_AUTH = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "label": "Anthropic (Claude)",
        "desc": "Best reasoning & safety",
        "has_oauth": True,
    },
    "google": {
        "env_var": "GOOGLE_API_KEY",
        "label": "Google (Gemini)",
        "desc": "Fast, multimodal, robotics",
        "has_oauth": True,
    },
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "label": "OpenAI (GPT)",
        "desc": "Widely supported",
    },
    "huggingface": {
        "env_var": "HF_TOKEN",
        "label": "Hugging Face",
        "desc": "Open-source models",
        "has_cli_login": True,
    },
    "ollama": {
        "env_var": None,
        "label": "Ollama (Local)",
        "desc": "Free, private, no API needed",
    },
    "llamacpp": {
        "env_var": None,
        "label": "llama.cpp (Local)",
        "desc": "Bare-metal GGUF inference",
    },
    "mlx": {
        "env_var": None,
        "label": "MLX (Apple Silicon)",
        "desc": "Native GPU, 400+ tok/s on Mac",
    },
}

# Ordered list for menu display
PROVIDER_ORDER = [
    "anthropic",
    "google",
    "openai",
    "huggingface",
    "ollama",
    "llamacpp",
    "mlx",
]

MODELS = {
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
            "label": "Gemini 3 Flash (Preview)",
            "desc": "Latest preview model",
            "tags": ["preview"],
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
    "ollama": [],  # dynamically populated or user enters name
    "llamacpp": [],  # user enters model name or GGUF path
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
}

SECONDARY_MODELS = [
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

PRESETS = {
    "1": None,  # Custom
    "2": "rpi_rc_car",
    "3": "waveshare_alpha",
    "4": "adeept_generic",
    "5": "freenove_4wd",
    "6": "sunfounder_picar",
}

CHANNELS = {
    "1": {
        "name": "whatsapp",
        "label": "WhatsApp (scan QR code)",
        "env_vars": [],
    },
    "2": {
        "name": "whatsapp_twilio",
        "label": "WhatsApp via Twilio (legacy)",
        "env_vars": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER"],
    },
    "3": {
        "name": "telegram",
        "label": "Telegram Bot",
        "env_vars": ["TELEGRAM_BOT_TOKEN"],
    },
    "4": {
        "name": "discord",
        "label": "Discord Bot",
        "env_vars": ["DISCORD_BOT_TOKEN"],
    },
    "5": {
        "name": "slack",
        "label": "Slack Bot",
        "env_vars": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
    },
}


def input_default(prompt, default):
    response = input(f"{prompt} [{default}]: ")
    return response if response else default


def input_secret(prompt):
    """Read a secret value (API key / token). Masks nothing but labels it clearly."""
    value = input(f"  {prompt}: ").strip()
    return value if value else None


# ---------------------------------------------------------------------------
# Provider selection (Step 2)
# ---------------------------------------------------------------------------
def choose_provider_step(default=None):
    """Select AI provider (separate from model)."""
    print(f"\n{Colors.GREEN}--- AI PROVIDER ---{Colors.ENDC}")
    print("Which AI provider do you want to use?\n")

    # Determine default selection number
    default_idx = "1"
    if default and default in PROVIDER_ORDER:
        default_idx = str(PROVIDER_ORDER.index(default) + 1)

    for i, key in enumerate(PROVIDER_ORDER, 1):
        info = PROVIDER_AUTH[key]
        prev_marker = " (previous)" if key == default else ""
        rec = " (Recommended)" if key == "anthropic" and not default else ""
        label = f"{info['label']}"
        desc = f"— {info['desc']}"
        print(f"  [{i}] {label:<28s} {desc}{rec}{prev_marker}")

    choice = input_default("\nSelection", default_idx).strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(PROVIDER_ORDER):
            return PROVIDER_ORDER[idx]
    except ValueError:
        pass
    return default or "anthropic"


# ---------------------------------------------------------------------------
# Authentication (Step 3)
# ---------------------------------------------------------------------------
def authenticate_provider(provider_key, *, already_authed=None):
    """Authenticate with a provider. Returns True if auth succeeded/skipped.

    *already_authed* is a set of provider keys already authenticated this session.
    """
    if already_authed is None:
        already_authed = set()

    if provider_key in already_authed:
        print(
            f"\n  {Colors.GREEN}[OK]{Colors.ENDC} "
            f"{PROVIDER_AUTH[provider_key]['label']} already authenticated."
        )
        return True

    info = PROVIDER_AUTH[provider_key]
    env_var = info.get("env_var")

    if not env_var:
        # Ollama — check connection
        print(f"\n{Colors.GREEN}--- AUTHENTICATION ({info['label']}) ---{Colors.ENDC}")
        print(f"  {Colors.GREEN}[OK]{Colors.ENDC} No API key needed for Ollama.")
        _check_ollama_connection()
        already_authed.add(provider_key)
        return True

    # Check if already in environment
    if os.getenv(env_var):
        print(f"\n  {Colors.GREEN}[OK]{Colors.ENDC} {env_var} already set in environment.")
        already_authed.add(provider_key)
        return True

    # Providers with interactive login flows
    if provider_key == "anthropic" and info.get("has_oauth"):
        result = _anthropic_auth_flow(env_var)
        if result:
            already_authed.add(provider_key)
        return result

    if provider_key == "google" and info.get("has_oauth"):
        result = _google_auth_flow(env_var)
        if result:
            already_authed.add(provider_key)
        return result

    if provider_key == "huggingface" and info.get("has_cli_login"):
        result = _huggingface_auth_flow(env_var)
        if result:
            already_authed.add(provider_key)
        return result

    # Check for existing keys from OpenClaw/environment
    existing = _detect_existing_keys()
    if env_var in existing:
        source, key = existing[env_var]
        masked = key[:12] + "..." + key[-4:]
        print(f"\n{Colors.GREEN}--- AUTHENTICATION ({info['label']}) ---{Colors.ENDC}")
        print(f"  Found API key from {Colors.BOLD}{source}{Colors.ENDC}: {masked}")
        use_it = input_default("Use this key? (y/n)", "y").strip().lower()
        if use_it in ("y", "yes", ""):
            _write_env_var(env_var, key)
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key imported from {source} and saved to .env")
            already_authed.add(provider_key)
            return True

    # Standard API key flow
    print(f"\n{Colors.GREEN}--- AUTHENTICATION ({info['label']}) ---{Colors.ENDC}")
    print(f"  Your {info['label']} API key is needed.")
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )

    key = input_secret(f"{env_var}")
    if key:
        valid = _validate_api_key(provider_key, key)
        _write_env_var(env_var, key)
        if valid:
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key validated and saved to .env")
        else:
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} Could not validate key "
                f"(network issue?). Saved to .env anyway."
            )
        already_authed.add(provider_key)
        return True
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")
        return False


def _check_ollama_connection():
    """Quick check if Ollama is reachable."""
    try:
        import httpx

        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Ollama is running.")
        else:
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} "
                f"Ollama responded with status {resp.status_code}."
            )
    except Exception:
        print(
            f"  {Colors.WARNING}[WARN]{Colors.ENDC} "
            f"Could not reach Ollama at localhost:11434. "
            f"Make sure it's running."
        )


def _detect_existing_keys():
    """Detect API keys from known sources (OpenClaw, environment, etc.)."""
    import json

    sources = {}

    # Check OpenClaw config (but NOT for Anthropic — use OpenCastor's own token)
    openclaw_config = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(openclaw_config):
        try:
            with open(openclaw_config) as f:
                data = json.load(f)
            env_vars = data.get("env", {}).get("vars", {})
            # Deliberately exclude ANTHROPIC_API_KEY — OpenClaw and OpenCastor
            # need separate Anthropic tokens to avoid the token sink problem.
            for key in [
                "GOOGLE_API_KEY",
                "OPENAI_API_KEY",
                "HF_TOKEN",
                "GEMINI_API_KEY",
            ]:
                val = env_vars.get(key)
                if val and not val.startswith("__") and len(val) > 10:
                    sources[key] = ("OpenClaw", val)
        except Exception:
            pass

    # Check environment
    for key in [
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "GEMINI_API_KEY",
    ]:
        val = os.getenv(key)
        if val and key not in sources and len(val) > 10:
            sources[key] = ("environment", val)

    return sources


def _anthropic_auth_flow(env_var):
    """Handle Anthropic auth: detect existing key or ask for one."""

    # Check for existing OpenCastor stored token first
    from castor.providers.anthropic_provider import AnthropicProvider

    stored = AnthropicProvider._read_stored_token()
    if stored:
        is_setup = stored.startswith(AnthropicProvider.SETUP_TOKEN_PREFIX)
        label = "setup-token (subscription)" if is_setup else "token"
        masked = stored[:16] + "..." + stored[-4:]
        print(f"\n{Colors.GREEN}--- AUTHENTICATION (Anthropic) ---{Colors.ENDC}")
        print(
            f"  Found existing {label} in "
            f"{Colors.BOLD}~/.opencastor/anthropic-token{Colors.ENDC}: {masked}"
        )
        use_it = input_default("Use this token? (y/n)", "y").strip().lower()
        if use_it in ("y", "yes", ""):
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Using existing {label}")
            return True

    print(f"\n{Colors.GREEN}--- AUTHENTICATION (Anthropic) ---{Colors.ENDC}")
    print("  Choose how to authenticate with Anthropic Claude:")
    print()
    print(
        f"  [1] Setup-token {Colors.BOLD}(Recommended — uses your Max/Pro subscription){Colors.ENDC}"
    )
    print("      Run 'claude setup-token' and paste the token. No per-token billing.")
    print("  [2] API key (pay-as-you-go)")
    print(
        f"      Get one at: {Colors.BOLD}https://console.anthropic.com/settings/keys{Colors.ENDC}"
    )
    print("  [3] I'll set it later (skip)")

    auth_choice = input_default("Selection", "1").strip()

    if auth_choice == "3":
        print(
            f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set ANTHROPIC_API_KEY in .env before running."
        )
        return False

    if auth_choice == "1":
        # Setup-token flow — stored in OpenCastor's own token file, not .env
        # This avoids the "token sink" problem where sharing tokens with
        # OpenClaw / Claude CLI causes mutual invalidation.
        print()
        print(f"  Run {Colors.BOLD}claude setup-token{Colors.ENDC} in another terminal,")
        print("  then paste the generated token (starts with sk-ant-oat01-).")
        print(
            f"  It will be saved to {Colors.BOLD}~/.opencastor/anthropic-token{Colors.ENDC}"
            f" (separate from Claude CLI / OpenClaw)."
        )
        key = input_secret("Setup-token")
        if key:
            from castor.providers.anthropic_provider import AnthropicProvider

            saved_path = AnthropicProvider.save_token(key)
            if key.startswith("sk-ant-oat01-") and len(key) >= 80:
                print(
                    f"  {Colors.GREEN}[OK]{Colors.ENDC} Setup-token saved to {saved_path} "
                    f"(subscription auth — no per-token billing)"
                )
                return True
            else:
                print(
                    f"  {Colors.WARNING}[WARN]{Colors.ENDC} Token doesn't look like a "
                    f"setup-token (expected sk-ant-oat01-...). Saved to {saved_path} anyway."
                )
                return True
        else:
            print(f"  {Colors.WARNING}Skipped.{Colors.ENDC}")
            return False

    # API key flow (choice "2" or anything else)
    print()
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )
    key = input_secret(f"{env_var}")
    if key:
        valid = _validate_api_key("anthropic", key)
        _write_env_var(env_var, key)
        if valid:
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key validated and saved to .env")
        else:
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} Could not validate key "
                f"(network issue?). Saved to .env anyway."
            )
        return True
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")
        return False


def _check_google_adc():
    """Check for existing Google Application Default Credentials."""
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if os.path.exists(adc_path):
        return True
    # Also check the environment variable
    adc_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if adc_env and os.path.exists(adc_env):
        return True
    return False


def _run_gcloud_login():
    """Run gcloud auth application-default login."""
    import shutil
    import subprocess

    if not shutil.which("gcloud"):
        return "not_installed"

    print(f"\n  {Colors.BOLD}Launching Google sign-in...{Colors.ENDC}")
    print("  A browser window will open. Sign in with your Google account.\n")
    try:
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "login"],
            timeout=120,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  {Colors.WARNING}Login failed: {e}{Colors.ENDC}")
        return False


def _google_auth_flow(env_var):
    """Handle Google auth: ADC/OAuth or API key."""
    print(f"\n{Colors.GREEN}--- AUTHENTICATION (Google) ---{Colors.ENDC}")
    print("  How would you like to authenticate with Google?")
    print("  [1] Google AI Studio subscription (sign in with Google account)")
    print("  [2] API key (paste GOOGLE_API_KEY)")

    auth_choice = input_default("Selection", "1").strip()

    if auth_choice == "1":
        # Check for existing ADC
        if _check_google_adc():
            print(
                f"\n  {Colors.GREEN}[OK]{Colors.ENDC} Google Application Default Credentials found."
            )
            _write_env_var("GOOGLE_AUTH_MODE", "adc")
            return True

        # Try gcloud login
        result = _run_gcloud_login()
        if result is True:
            print(
                f"\n  {Colors.GREEN}[OK]{Colors.ENDC} "
                f"Signed in! Using Application Default Credentials."
            )
            _write_env_var("GOOGLE_AUTH_MODE", "adc")
            return True
        elif result == "not_installed":
            print(
                f"\n  {Colors.WARNING}gcloud CLI not found.{Colors.ENDC} Falling back to API key."
            )
            print(f"  Install: {Colors.BOLD}https://cloud.google.com/sdk/docs/install{Colors.ENDC}")
            print(f"  Then run: {Colors.BOLD}gcloud auth application-default login{Colors.ENDC}\n")
        else:
            print(f"  {Colors.WARNING}Login failed.{Colors.ENDC} Falling back to API key.")

    # Fall through to API key
    print("\n  Your Google API key is needed.")
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )
    key = input_secret(f"{env_var}")
    if key:
        valid = _validate_api_key("google", key)
        _write_env_var(env_var, key)
        if valid:
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key validated and saved to .env")
        else:
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} Could not validate key "
                f"(network issue?). Saved to .env anyway."
            )
        return True
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")
        return False


def _check_huggingface_token():
    """Check for existing HuggingFace token."""
    # New location (huggingface_hub >= 0.14)
    token_path = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(token_path):
        with open(token_path) as f:
            token = f.read().strip()
        if token:
            return True
    # Legacy location
    legacy_path = os.path.expanduser("~/.huggingface/token")
    if os.path.exists(legacy_path):
        with open(legacy_path) as f:
            token = f.read().strip()
        if token:
            return True
    return False


def _run_huggingface_login():
    """Run huggingface-cli login."""
    import shutil
    import subprocess

    if not shutil.which("huggingface-cli"):
        return "not_installed"

    print(f"\n  {Colors.BOLD}Launching Hugging Face login...{Colors.ENDC}")
    print("  A browser window will open. Sign in with your Hugging Face account.\n")
    try:
        result = subprocess.run(
            ["huggingface-cli", "login"],
            timeout=120,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  {Colors.WARNING}Login failed: {e}{Colors.ENDC}")
        return False


def _huggingface_auth_flow(env_var):
    """Handle HuggingFace auth: CLI login or paste token."""
    print(f"\n{Colors.GREEN}--- AUTHENTICATION (Hugging Face) ---{Colors.ENDC}")
    print("  How would you like to authenticate with Hugging Face?")
    print("  [1] Sign in with Hugging Face account (opens browser)")
    print("  [2] Paste token (HF_TOKEN)")

    auth_choice = input_default("Selection", "1").strip()

    if auth_choice == "1":
        # Check for existing token
        if _check_huggingface_token():
            print(
                f"\n  {Colors.GREEN}[OK]{Colors.ENDC} "
                f"Hugging Face token found (~/.cache/huggingface/token)."
            )
            _write_env_var("HF_AUTH_MODE", "cli")
            return True

        # Try CLI login
        result = _run_huggingface_login()
        if result is True:
            print(f"\n  {Colors.GREEN}[OK]{Colors.ENDC} Signed in! Token saved by huggingface-cli.")
            _write_env_var("HF_AUTH_MODE", "cli")
            return True
        elif result == "not_installed":
            print(
                f"\n  {Colors.WARNING}huggingface-cli not found.{Colors.ENDC} "
                f"Falling back to token."
            )
            print(f"  Install: {Colors.BOLD}pip install huggingface_hub{Colors.ENDC}")
            print(f"  Then run: {Colors.BOLD}huggingface-cli login{Colors.ENDC}\n")
        else:
            print(f"  {Colors.WARNING}Login failed.{Colors.ENDC} Falling back to token.")

    # Fall through to paste token
    print("\n  Your Hugging Face token is needed.")
    print(f"  Get one at: {Colors.BOLD}https://huggingface.co/settings/tokens{Colors.ENDC}")
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )
    key = input_secret(f"{env_var}")
    if key:
        _write_env_var(env_var, key)
        print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Token saved to .env")
        return True
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")
        return False


# ---------------------------------------------------------------------------
# Model selection (Step 4)
# ---------------------------------------------------------------------------
def choose_model(provider_key):
    """Choose primary model for the selected provider."""
    if provider_key == "ollama":
        return _choose_ollama_model()

    # Try dynamic model list for Anthropic and OpenAI
    if provider_key in ("anthropic", "openai"):
        result = _choose_model_dynamic(provider_key)
        if result:
            return result
        # Fall through to static list if API fetch failed

    models = MODELS.get(provider_key, [])
    if not models:
        name = input_default("Enter model name/ID", "")
        return {"id": name, "label": name, "desc": "", "tags": []}

    return _present_model_menu(models)


def _present_model_menu(models, show_expand=False):
    """Display a model selection menu and return the chosen model."""
    print(f"\n{Colors.GREEN}--- PRIMARY MODEL (Chat & Reasoning) ---{Colors.ENDC}")
    for i, m in enumerate(models, 1):
        rec = " (Recommended)" if m.get("recommended") else ""
        print(f"  [{i}] {m['label']:<28s} ({m['desc']}){rec}")

    choice = input_default("\nSelection", "1").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            return models[idx]
    except ValueError:
        pass
    return models[0]


def _choose_model_dynamic(provider_key):
    """Fetch latest models from Anthropic/OpenAI API and present top 3 + expand option."""
    print(f"\n  Fetching latest models from {provider_key}...", end="", flush=True)

    try:
        if provider_key == "anthropic":
            all_models = _fetch_anthropic_models()
        else:
            all_models = _fetch_openai_models()
    except Exception as e:
        print(f" {Colors.WARNING}failed ({e}){Colors.ENDC}")
        print("  Falling back to built-in model list.")
        return None

    if not all_models:
        print(f" {Colors.WARNING}no models found{Colors.ENDC}")
        return None

    print(f" found {len(all_models)} models")

    # Show top 3, mark first as recommended
    top = all_models[:3]
    top[0]["recommended"] = True

    print(f"\n{Colors.GREEN}--- PRIMARY MODEL (Chat & Reasoning) ---{Colors.ENDC}")
    print("  Latest models (live from API):")
    for i, m in enumerate(top, 1):
        rec = " (Recommended)" if m.get("recommended") else ""
        print(f"  [{i}] {m['label']:<32s} ({m['desc']}){rec}")
    print(f"  [{len(top) + 1}] Show all {len(all_models)} models")
    print(f"  [{len(top) + 2}] Enter model ID manually")

    choice = input_default("\nSelection", "1").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(top):
            return top[idx]
        if idx == len(top):
            # Show full list
            return _choose_from_full_list(all_models)
        if idx == len(top) + 1:
            name = input_default("Enter model ID", "")
            if name:
                return {"id": name, "label": name, "desc": "Custom", "tags": []}
    except ValueError:
        pass
    return top[0]


def _choose_from_full_list(models):
    """Present the full model list with pagination-friendly display."""
    print(f"\n{Colors.GREEN}--- ALL AVAILABLE MODELS ---{Colors.ENDC}")
    for i, m in enumerate(models, 1):
        print(f"  [{i:2d}] {m['label']:<32s} ({m['desc']})")

    choice = input_default("\nSelection", "1").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            return models[idx]
    except ValueError:
        pass
    return models[0]


def _fetch_anthropic_models():
    """Fetch latest Anthropic models from the public docs page.

    We don't use the /v1/models API because setup-tokens (subscription auth)
    return 401 on that endpoint. Instead, we fetch the public model listing
    which doesn't require authentication.
    """
    import re

    try:
        req = Request(
            "https://docs.anthropic.com/en/docs/about-claude/models",
            headers={"User-Agent": "OpenCastor-Wizard"},
        )
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    # Parse model IDs from the docs page — look for claude-* model strings
    # Match patterns like claude-opus-4-6, claude-sonnet-4-5-20250929, etc.
    model_pattern = re.compile(r"(claude-(?:opus|sonnet|haiku)-[\w.-]+)")
    found_ids = list(dict.fromkeys(model_pattern.findall(html)))  # dedupe, preserve order

    if not found_ids:
        return []

    # Build model entries with clean labels
    models = []
    seen = set()
    for model_id in found_ids:
        # Skip duplicates and overly long IDs (likely CSS/URL fragments)
        if model_id in seen or len(model_id) > 50:
            continue
        seen.add(model_id)

        # Generate a readable label
        label = model_id.replace("-", " ").title()
        # Clean up common patterns
        label = label.replace("Claude ", "Claude ")
        models.append(
            {
                "id": model_id,
                "label": label,
                "desc": "",
                "tags": [],
            }
        )

    return models[:15]  # Cap at 15 most relevant


def _fetch_openai_models():
    """Fetch models from OpenAI API. Returns chat models sorted by newest first."""
    import json

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []

    req = Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    # Filter to chat-relevant models (gpt-*, o1-*, o3-*, chatgpt-*)
    # Skip fine-tunes, embeddings, tts, whisper, dall-e, etc.
    chat_prefixes = ("gpt-4", "gpt-3.5", "o1", "o3", "o4", "chatgpt")
    skip_suffixes = ("-instruct", "-realtime", "-audio", "-transcribe", "-search")
    models = []
    for m in data.get("data", []):
        model_id = m["id"]
        if not any(model_id.startswith(p) for p in chat_prefixes):
            continue
        if any(model_id.endswith(s) for s in skip_suffixes):
            continue
        # Skip fine-tuned models
        if ":ft-" in model_id or "ft:" in model_id:
            continue
        created = m.get("created", 0)
        models.append(
            {
                "id": model_id,
                "label": model_id,
                "desc": "",
                "tags": [],
                "_created": created,
            }
        )

    # Sort newest first
    models.sort(key=lambda x: x.get("_created", 0), reverse=True)

    # Clean up internal key
    for m in models:
        m.pop("_created", None)

    return models


def _choose_ollama_model():
    """List locally available Ollama models or let user type a name."""
    print(f"\n{Colors.GREEN}--- PRIMARY MODEL (Ollama) ---{Colors.ENDC}")

    local_models = _list_ollama_models()
    if local_models:
        print("  Locally available models:")
        for i, name in enumerate(local_models, 1):
            print(f"  [{i}] {name}")
        print(f"  [{len(local_models) + 1}] Other (type model name)")
        choice = input_default("\nSelection", "1").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(local_models):
                name = local_models[idx]
                return {"id": name, "label": name, "desc": "Local", "tags": ["local"]}
        except ValueError:
            pass

    name = input_default("Enter Ollama model name", "llava:13b")
    return {"id": name, "label": name, "desc": "Local", "tags": ["local"]}


def _list_ollama_models():
    """Fetch locally available Ollama models."""
    try:
        import httpx

        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Secondary models (Step 5)
# ---------------------------------------------------------------------------
def choose_secondary_models(primary_provider, already_authed):
    """Optionally add secondary/specialized models."""
    print(f"\n{Colors.GREEN}--- SECONDARY MODELS (optional) ---{Colors.ENDC}")
    print("  Add specialized models for vision, robotics, or embeddings.\n")
    print("  [0] Skip")
    for i, m in enumerate(SECONDARY_MODELS, 1):
        print(f"  [{i}] {m['label']:<38s} — {m['desc']}")
    print(f"  [{len(SECONDARY_MODELS) + 1}] Custom (enter provider + model name)")

    choice = input_default("\nSelection (comma-separated, e.g. 1,2)", "0").strip()
    if choice == "0":
        return []

    selected = []
    for c in choice.split(","):
        c = c.strip()
        try:
            idx = int(c) - 1
            if idx == len(SECONDARY_MODELS):
                # Custom entry
                custom = _add_custom_secondary(already_authed)
                if custom:
                    selected.append(custom)
            elif 0 <= idx < len(SECONDARY_MODELS):
                sm = SECONDARY_MODELS[idx]
                # Auth if needed
                if sm["provider"] != primary_provider:
                    authenticate_provider(sm["provider"], already_authed=already_authed)
                selected.append(
                    {
                        "provider": sm["provider"],
                        "model": sm["id"],
                        "label": sm["label"],
                        "tags": sm["tags"],
                    }
                )
        except ValueError:
            continue

    return selected


def _add_custom_secondary(already_authed):
    """Prompt for a custom secondary model."""
    print("\n  Available providers: anthropic, google, openai, huggingface, ollama")
    provider = input_default("  Provider", "google").strip().lower()
    model_id = input_default("  Model ID", "").strip()
    if not model_id:
        return None
    if provider in PROVIDER_AUTH:
        authenticate_provider(provider, already_authed=already_authed)
    return {
        "provider": provider,
        "model": model_id,
        "label": f"{provider}/{model_id}",
        "tags": ["custom"],
    }


# ---------------------------------------------------------------------------
# Brain Architecture (Step 6)
# ---------------------------------------------------------------------------

# Pre-built tiered brain configurations
BRAIN_PRESETS = [
    {
        "name": "Free & Open Source",
        "desc": "$0/mo — HuggingFace API + Ollama fallback",
        "primary": {"provider": "huggingface", "model": "Qwen/Qwen2.5-VL-7B-Instruct"},
        "planner": None,
        "cost": "free",
    },
    {
        "name": "Budget Smart",
        "desc": "~$0/mo — HF primary + Gemini Flash-Lite fallback (free tiers)",
        "primary": {"provider": "huggingface", "model": "Qwen/Qwen2.5-VL-7B-Instruct"},
        "planner": {"provider": "google", "model": "gemini-2.5-flash-lite"},
        "cost": "free",
    },
    {
        "name": "Balanced (Recommended)",
        "desc": "~$5/mo — HF primary + Claude planner for complex reasoning",
        "primary": {"provider": "huggingface", "model": "Qwen/Qwen2.5-VL-7B-Instruct"},
        "planner": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "cost": "low",
    },
    {
        "name": "Performance",
        "desc": "~$10-20/mo — Gemini Flash primary + Claude planner",
        "primary": {"provider": "google", "model": "gemini-2.5-flash-lite"},
        "planner": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "cost": "medium",
    },
    {
        "name": "Maximum Intelligence",
        "desc": "$$$ — Claude primary + Claude Opus planner",
        "primary": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "planner": {"provider": "anthropic", "model": "claude-opus-4-6"},
        "cost": "high",
    },
]


def choose_brain_architecture(primary_provider, secondary_models, already_authed):
    """Guide user through tiered brain setup for cost-effective AI."""
    print(f"\n{Colors.GREEN}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.BOLD}  🧠 BRAIN ARCHITECTURE{Colors.ENDC}")
    print(f"{Colors.GREEN}{'=' * 60}{Colors.ENDC}")
    print()
    print("  OpenCastor uses a tiered brain for cost-effective AI:")
    print()
    print(f"  {Colors.BOLD}Layer 0:{Colors.ENDC} Reactive    (<1ms)   — Rule-based safety, free")
    print(
        f"  {Colors.BOLD}Layer 1:{Colors.ENDC} Fast Brain  (~500ms) — Primary perception + action"
    )
    print(
        f"  {Colors.BOLD}Layer 2:{Colors.ENDC} Planner     (~10s)   — Complex reasoning (periodic)"
    )
    print()
    print("  The fast brain handles every frame. The planner runs every")
    print("  ~15 ticks or when the fast brain is uncertain.")
    print()
    print(f"  {Colors.GREEN}Choose a cost tier:{Colors.ENDC}")
    print()

    for i, preset in enumerate(BRAIN_PRESETS, 1):
        marker = " ★" if "Recommended" in preset["name"] else ""
        print(f"  [{i}] {preset['name']:<28s} {preset['desc']}{marker}")

    print("  [0] Skip (use primary model for everything)")
    print()

    choice = input_default("Selection", "3").strip()

    if choice == "0":
        return {}

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(BRAIN_PRESETS):
            preset = BRAIN_PRESETS[idx]
        else:
            return {}
    except ValueError:
        return {}

    result = {
        "tiered_brain": {
            "planner_interval": 15,
            "uncertainty_threshold": 0.3,
        },
    }

    # If preset primary differs from wizard primary, inform user
    bp = preset["primary"]
    if bp["provider"] != primary_provider:
        print(f"\n  This preset uses {Colors.BOLD}{bp['provider']}/{bp['model']}{Colors.ENDC}")
        print("  as the fast brain (overrides your primary provider selection).")
        # Auth the fast brain provider
        authenticate_provider(bp["provider"], already_authed=already_authed)

    # Override the agent config with the fast brain
    result["agent_override"] = {
        "provider": bp["provider"],
        "model": bp["model"],
        "vision_enabled": True,
    }

    # Set up planner as secondary
    if preset["planner"]:
        pp = preset["planner"]
        if pp["provider"] not in already_authed and pp["provider"] != primary_provider:
            authenticate_provider(pp["provider"], already_authed=already_authed)

        # Add planner to secondary models if not already there
        planner_entry = {
            "provider": pp["provider"],
            "model": pp["model"],
            "tags": ["planning", "reasoning"],
        }
        result["planner_secondary"] = planner_entry

    # Hailo-8 vision (auto-detect)
    try:
        import hailo_platform  # noqa: F401

        if os.path.exists("/usr/share/hailo-models/yolov8s_h8.hef"):
            print(f"\n  {Colors.GREEN}✓ Hailo-8 NPU detected!{Colors.ENDC}")
            print("    Enabling hardware-accelerated object detection (~250ms)")
            print("    80 COCO classes • obstacle avoidance • zero API cost")
            result["reactive"] = {
                "hailo_vision": True,
                "hailo_confidence": 0.4,
                "min_obstacle_m": 0.3,
            }
    except ImportError:
        pass

    print(f"\n  {Colors.GREEN}✓ Brain architecture configured!{Colors.ENDC}")
    cost = preset["cost"]
    if cost == "free":
        print(f"    Estimated cost: {Colors.BOLD}$0/month{Colors.ENDC} 🎉")
    elif cost == "low":
        print(f"    Estimated cost: {Colors.BOLD}~$5/month{Colors.ENDC}")
    elif cost == "medium":
        print(f"    Estimated cost: {Colors.BOLD}~$10-20/month{Colors.ENDC}")
    else:
        print(f"    Estimated cost: {Colors.BOLD}varies (pay-per-use){Colors.ENDC}")

    return result


# ---------------------------------------------------------------------------
# Legacy choose_provider — used by Advanced flow
# ---------------------------------------------------------------------------
def choose_provider():
    """Legacy provider+model selection (used by Advanced flow)."""
    print(f"\n{Colors.GREEN}--- BRAIN SELECTION ---{Colors.ENDC}")
    print("Which AI provider do you want to use?")
    for key, val in PROVIDERS.items():
        rec = " (Recommended)" if key == "1" else ""
        print(f"  [{key}] {val['label']}{rec}")

    choice = input_default("Selection", "1")
    return PROVIDERS.get(choice, PROVIDERS["1"])


def _validate_api_key(provider: str, api_key: str) -> bool:
    """Make a lightweight test call to validate an API key."""
    if not api_key:
        return False

    try:
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            client.models.list(limit=1)
            return True
        elif provider == "google":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            list(genai.list_models())
            return True
        elif provider == "openai":
            import openai

            client = openai.OpenAI(api_key=api_key)
            client.models.list()
            return True
        elif provider == "openrouter":
            import httpx

            resp = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
    except Exception:
        return False

    return False


def _check_claude_oauth():
    """Check if Claude CLI is installed and authenticated (Max/Pro plan)."""
    import shutil
    import subprocess

    if not shutil.which("claude"):
        return None
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
    except Exception:
        return None

    # Use claude auth status to check if signed in
    try:
        auth_result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if auth_result.returncode == 0 and "logged in" in auth_result.stdout.lower():
            return True
    except Exception:
        pass

    # Fallback: check for credential files
    claude_creds = os.path.expanduser("~/.claude/credentials.json")
    claude_settings = os.path.expanduser("~/.claude/settings.json")
    if os.path.exists(claude_creds) or os.path.exists(claude_settings):
        return True

    return "installed"


def _run_claude_login():
    """Run claude CLI OAuth login flow."""
    import subprocess

    print(f"\n  {Colors.BOLD}Launching Claude login...{Colors.ENDC}")
    print("  A browser window will open. Sign in with your Anthropic account.\n")
    try:
        result = subprocess.run(
            ["claude", "auth", "login"],
            timeout=120,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  {Colors.WARNING}Login failed: {e}{Colors.ENDC}")
        return False


def collect_api_key(agent_config):
    """Prompt the user for their provider API key and write it to .env.

    Used by the Advanced flow (legacy path).
    """
    env_var = agent_config.get("env_var")
    if not env_var:
        return

    if os.getenv(env_var):
        print(f"\n  {Colors.GREEN}[OK]{Colors.ENDC} {env_var} already set in environment.")
        return

    if agent_config.get("provider") == "anthropic":
        _anthropic_auth_flow(env_var)
        return

    print(f"\n{Colors.GREEN}--- API KEY ---{Colors.ENDC}")
    print(f"  Your {agent_config['label']} API key is needed.")
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )

    key = input_secret(f"{env_var}")
    if key:
        provider = agent_config.get("provider", "")
        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
                console=_console,
            ) as progress:
                progress.add_task(description="Validating API key...", total=None)
                valid = _validate_api_key(provider, key)
        else:
            print("  Validating API key...", end=" ", flush=True)
            valid = _validate_api_key(provider, key)

        if valid:
            _write_env_var(env_var, key)
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key validated and saved to .env")
        else:
            _write_env_var(env_var, key)
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} Could not validate key "
                f"(network issue?). Saved to .env anyway."
            )
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")


def choose_channels():
    """Ask which messaging channels to enable."""
    print(f"\n{Colors.GREEN}--- MESSAGING CHANNELS ---{Colors.ENDC}")
    print("Connect your robot to messaging platforms (optional).")
    print("You can enable multiple channels. Enter numbers separated by commas.")
    print("  [0] None (skip)")
    for key, val in CHANNELS.items():
        print(f"  [{key}] {val['label']}")

    choice = input_default("Selection (e.g. 1,2)", "0").strip()
    if choice == "0":
        return []

    selected = []
    for c in choice.split(","):
        c = c.strip()
        if c in CHANNELS:
            selected.append(CHANNELS[c])
    return selected


def collect_channel_credentials(channels):
    """Set up and verify each selected messaging channel."""
    if not channels:
        return

    print(f"\n{Colors.GREEN}--- CHANNEL CREDENTIALS ---{Colors.ENDC}")
    print(f"  Credentials will be saved to {Colors.BOLD}~/.opencastor/env{Colors.ENDC}.\n")

    for ch in channels:
        name = ch["name"]
        print(f"  {Colors.BOLD}{ch['label']}{Colors.ENDC}")

        if name == "whatsapp":
            _setup_whatsapp()
            continue

        if name == "telegram":
            _setup_telegram()
            continue

        # Generic: collect env vars
        if not ch["env_vars"]:
            print(f"    {Colors.GREEN}[OK]{Colors.ENDC} No credentials needed")
            print()
            continue
        for env_var in ch["env_vars"]:
            if os.getenv(env_var):
                print(f"    {Colors.GREEN}[OK]{Colors.ENDC} {env_var} already set")
                continue
            value = input_secret(env_var)
            if value:
                _write_env_var(env_var, value)
                print(f"    {Colors.GREEN}[OK]{Colors.ENDC} Saved")
            else:
                print(f"    {Colors.WARNING}Skipped{Colors.ENDC}")
        print()


def _setup_whatsapp():
    """Set up WhatsApp channel: verify neonize, check session, explain QR flow."""

    print()

    # 1. Check if neonize is installed
    neonize_ok = False
    try:
        import neonize  # noqa: F401

        neonize_ok = True
        print(f"    {Colors.GREEN}[OK]{Colors.ENDC} neonize package installed")
    except ImportError:
        print(f"    {Colors.WARNING}[WARN]{Colors.ENDC} neonize not installed")
        print("    Installing neonize (WhatsApp Web protocol)...")
        import subprocess

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "neonize>=0.3.10"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                neonize_ok = True
                print(f"    {Colors.GREEN}[OK]{Colors.ENDC} neonize installed successfully")
            else:
                print(f"    {Colors.FAIL}[FAIL]{Colors.ENDC} Could not install neonize")
                print("    Try manually: pip install 'opencastor[whatsapp]'")
        except Exception as e:
            print(f"    {Colors.FAIL}[FAIL]{Colors.ENDC} Install failed: {e}")

    # 2. Check for existing WhatsApp session
    session_paths = [
        os.path.expanduser("~/.opencastor/whatsapp_session.db"),
        "whatsapp_session.db",
    ]
    session_exists = any(os.path.exists(p) for p in session_paths)
    if session_exists:
        print(
            f"    {Colors.GREEN}[OK]{Colors.ENDC} Existing WhatsApp session found (already paired)"
        )
    else:
        print(f"    {Colors.BLUE}[INFO]{Colors.ENDC} No existing session — QR pairing needed")

    # 3. Explain the QR flow
    print()
    print(f"    {Colors.BOLD}How WhatsApp pairing works:{Colors.ENDC}")
    print("    1. Run: castor gateway --config <your-config>.rcan.yaml")
    print("    2. A QR code will appear in your terminal")
    print("    3. Open WhatsApp on your phone → Settings → Linked Devices → Link a Device")
    print("    4. Scan the QR code — your robot is now connected!")
    print()

    if neonize_ok and not session_exists:
        print("    Would you like to pair WhatsApp now?")
        pair_now = input_default("    Start QR pairing? (y/n)", "n").strip().lower()
        if pair_now in ("y", "yes"):
            _run_whatsapp_pairing()
    print()


def _run_whatsapp_pairing():
    """Start a quick WhatsApp pairing session to scan QR code."""
    import subprocess

    print()
    print(f"    {Colors.BOLD}Starting WhatsApp pairing...{Colors.ENDC}")
    print("    A QR code will appear below. Scan it with your phone.")
    print("    Press Ctrl+C when pairing is complete.\n")

    try:
        # Run a minimal neonize client that just does QR pairing
        pairing_script = """
import os, sys
try:
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, PairStatusEv

    db_path = os.path.expanduser("~/.opencastor/whatsapp_session.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    client = NewClient(db_path)

    @client.event(ConnectedEv)
    def on_connected(client, event):
        print("\\n    ✅ WhatsApp connected successfully!")
        print("    You can now close this with Ctrl+C.\\n")

    @client.event(PairStatusEv)
    def on_pair(client, event):
        print("    📱 Pairing status update received")

    print("    Waiting for QR code scan...")
    client.connect()
except KeyboardInterrupt:
    print("\\n    Pairing session ended.")
except Exception as e:
    print(f"\\n    ⚠️  Pairing error: {e}")
"""
        subprocess.run(
            [sys.executable, "-c", pairing_script],
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("    ⚠️  Pairing timed out (2 min). Try again with: castor gateway")
    except KeyboardInterrupt:
        print("\n    Pairing session ended.")
    except Exception as e:
        print(f"    ⚠️  Could not start pairing: {e}")


def _setup_telegram():
    """Set up Telegram bot: collect token and verify via Bot API."""
    print()

    existing_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if existing_token:
        print(f"    {Colors.GREEN}[OK]{Colors.ENDC} TELEGRAM_BOT_TOKEN already set")
        # Verify it works
        _verify_telegram_token(existing_token)
        print()
        return

    print("    To create a Telegram bot:")
    print(f"    1. Open Telegram and message {Colors.BOLD}@BotFather{Colors.ENDC}")
    print("    2. Send /newbot and follow the prompts")
    print("    3. Copy the API token (looks like: 123456789:ABCdefGHI...)")
    print()

    token = input_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        print(f"    {Colors.WARNING}Skipped.{Colors.ENDC} Set TELEGRAM_BOT_TOKEN later.")
        print()
        return

    _write_env_var("TELEGRAM_BOT_TOKEN", token)
    print(f"    {Colors.GREEN}[OK]{Colors.ENDC} Token saved")

    # Verify
    _verify_telegram_token(token)
    print()


def _verify_telegram_token(token):
    """Verify a Telegram bot token by calling getMe."""
    import json

    try:
        req = Request(f"https://api.telegram.org/bot{token}/getMe")
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("ok"):
            bot = data["result"]
            name = bot.get("first_name", "Unknown")
            username = bot.get("username", "?")
            print(f"    {Colors.GREEN}[OK]{Colors.ENDC} Bot verified: {name} (@{username})")
        else:
            print(f"    {Colors.WARNING}[WARN]{Colors.ENDC} Token not recognized by Telegram")
    except Exception as e:
        print(f"    {Colors.WARNING}[WARN]{Colors.ENDC} Could not verify token: {e}")


def choose_hardware():
    """Select hardware kit, with optional auto-detection."""
    print(f"\n{Colors.GREEN}--- HARDWARE KIT ---{Colors.ENDC}")

    try:
        from castor.hardware_detect import detect_hardware, suggest_preset

        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
                console=_console,
            ) as progress:
                progress.add_task(description="Scanning for hardware...", total=None)
                hw = detect_hardware()
        else:
            print("  Scanning for hardware...", end=" ", flush=True)
            hw = detect_hardware()

        preset_name, confidence, reason = suggest_preset(hw)

        if confidence in ("high", "medium"):
            print(f"\n  {Colors.GREEN}[AUTO-DETECT]{Colors.ENDC} {reason}")
            print(f"  Suggested preset: {Colors.BOLD}{preset_name}{Colors.ENDC}")
            use_detected = input_default("Use detected hardware?", "Y").strip().lower()
            if use_detected in ("y", "yes", ""):
                return preset_name
            print()
        else:
            print(f"\n  {Colors.WARNING}[AUTO-DETECT]{Colors.ENDC} {reason}")
            print("  Falling back to manual selection.\n")
    except Exception:
        pass

    print("Select your hardware kit:")
    print("  [1] Custom (Advanced)")
    print("  [2] RPi RC Car + PCA9685 + CSI Camera (Recommended)")
    print("  [3] Waveshare AlphaBot ($45)")
    print("  [4] Adeept RaspTank ($55)")
    print("  [5] Freenove 4WD Car ($49)")
    print("  [6] SunFounder PiCar-X ($60)")

    choice = input_default("Selection", "2")
    return PRESETS.get(choice)


def get_kinematics():
    print(f"\n{Colors.GREEN}--- KINEMATICS SETUP ---{Colors.ENDC}")
    dof = int(input_default("How many Degrees of Freedom (DoF)?", "6"))

    links = []
    print(f"Defining {dof} links (Base -> End Effector)...")

    for i in range(dof):
        print(f"\n{Colors.BOLD}Link {i + 1}{Colors.ENDC}")
        length = input_default("  Length (mm)", "100")
        mass = input_default("  Approx Mass (g)", "50")
        axis = input_default("  Rotation Axis (x/y/z)", "z")

        links.append(
            {
                "id": f"link_{i + 1}",
                "length_mm": float(length),
                "mass_g": float(mass),
                "axis": axis,
            }
        )
    return links


def get_drivers(links):
    print(f"\n{Colors.GREEN}--- DRIVER MAPPING ---{Colors.ENDC}")
    print("Mapping physical motors to kinematic links...")

    drivers = []
    protocol = input_default(
        "Default Protocol (dynamixel/serial/canbus/ros2/pca9685_i2c)", "serial"
    )
    port = input_default("Default Port (e.g., /dev/ttyUSB0)", "/dev/ttyUSB0")

    for i, link in enumerate(links):
        print(f"\nConfiguring motor for {Colors.BOLD}{link['id']}{Colors.ENDC}")
        motor_id = input_default("  Motor ID", str(i + 1))

        drivers.append(
            {
                "link_id": link["id"],
                "protocol": protocol,
                "port": port,
                "hardware_id": int(motor_id),
                "baud_rate": 115200,
            }
        )
    return drivers


def _build_agent_config(provider_key, model_info):
    """Build the agent_config dict from new-style provider + model selection.

    Maintains backward compatibility: returns dict with provider, model, label, env_var.
    """
    info = PROVIDER_AUTH[provider_key]
    return {
        "provider": provider_key,
        "model": model_info["id"],
        "label": f"{info['label'].split('(')[0].strip()} {model_info['label']}",
        "env_var": info["env_var"],
    }


def generate_preset_config(preset_name, robot_name, agent_config, secondary_models=None):
    """Generate config for a known hardware preset."""
    preset_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        "presets",
        f"{preset_name}.rcan.yaml",
    )
    if os.path.exists(preset_path):
        with open(preset_path) as f:
            config = yaml.safe_load(f)
        config["metadata"]["robot_name"] = robot_name
        config["metadata"]["robot_uuid"] = str(uuid.uuid4())
        config["metadata"]["created_at"] = datetime.now(timezone.utc).isoformat()
        config["agent"]["provider"] = agent_config["provider"]
        config["agent"]["model"] = agent_config["model"]
    else:
        config = {
            "rcan_version": "1.0.0-alpha",
            "metadata": {
                "robot_name": robot_name,
                "robot_uuid": str(uuid.uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "author": "OpenCastor Wizard",
                "license": "Apache-2.0",
                "tags": ["mobile", "rover", "amazon_kit"],
            },
            "agent": {
                "provider": agent_config["provider"],
                "model": agent_config["model"],
                "vision_enabled": True,
                "latency_budget_ms": 200,
                "safety_stop": True,
            },
            "physics": {
                "type": "differential_drive",
                "dof": 2,
                "chassis": {
                    "wheel_base_mm": 150,
                    "wheel_radius_mm": 32,
                },
            },
            "drivers": [
                {
                    "id": "motor_driver",
                    "protocol": "pca9685_i2c",
                    "port": "/dev/i2c-1",
                    "address": "0x40",
                    "frequency": 50,
                    "channels": {
                        "left_front": 0,
                        "left_rear": 1,
                        "right_front": 2,
                        "right_rear": 3,
                    },
                }
            ],
            "network": {
                "telemetry_stream": True,
                "sim_to_real_sync": True,
                "allow_remote_override": False,
            },
            "rcan_protocol": {
                "port": 8000,
                "capabilities": ["status", "nav", "teleop", "chat"],
                "enable_mdns": False,
                "enable_jwt": False,
            },
        }

    # Add secondary models if any
    if secondary_models:
        config["agent"]["secondary_models"] = [
            {"provider": sm["provider"], "model": sm["model"], "tags": sm.get("tags", [])}
            for sm in secondary_models
        ]

    return config


def generate_custom_config(robot_name, agent_config, links, drivers):
    """Generate config for custom hardware."""
    return {
        "rcan_version": "1.0.0-alpha",
        "metadata": {
            "robot_name": robot_name,
            "robot_uuid": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "author": "OpenCastor Wizard",
            "license": "Apache-2.0",
        },
        "agent": {
            "provider": agent_config["provider"],
            "model": agent_config["model"],
            "vision_enabled": True,
            "latency_budget_ms": 200,
            "safety_stop": True,
        },
        "physics": {
            "type": "serial_manipulator",
            "dof": len(links),
            "kinematics": links,
            "dynamics": {
                "gravity": [0, 0, -9.81],
                "payload_capacity_g": 500,
            },
        },
        "drivers": drivers,
        "network": {
            "telemetry_stream": True,
            "sim_to_real_sync": True,
            "allow_remote_override": False,
        },
        "rcan_protocol": {
            "port": 8000,
            "capabilities": ["status", "arm", "chat"],
            "enable_mdns": False,
            "enable_jwt": False,
        },
    }


ENV_DIR = os.path.expanduser("~/.opencastor")
ENV_PATH = os.path.join(ENV_DIR, "env")


def _write_env_var(key: str, value: str):
    """Write or update a variable in ~/.opencastor/env.

    Credentials are stored in the user's home directory (not the install dir)
    so they survive uninstall/reinstall. The file has 0600 permissions.
    Also writes to local .env for backward compatibility.
    """
    # Primary: ~/.opencastor/env (survives uninstall)
    os.makedirs(ENV_DIR, mode=0o700, exist_ok=True)
    _upsert_env_file(ENV_PATH, key, value)
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass

    # Secondary: local .env (backward compat for castor run in project dir)
    _upsert_env_file(".env", key, value)


def _upsert_env_file(env_path: str, key: str, value: str):
    """Insert or update a key=value in an env file."""
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


def _safety_acknowledgment(accept_risk):
    """Show safety warning and require acknowledgment before proceeding."""
    if accept_risk:
        return

    if HAS_RICH:
        _console.print(
            Panel(
                "[bold yellow]SAFETY WARNING[/]\n\n"
                "  OpenCastor controls [bold]PHYSICAL MOTORS[/] and [bold]SERVOS[/].\n"
                "  Before continuing, please ensure:\n\n"
                "    [yellow]-[/] Keep hands and cables clear of moving parts\n"
                "    [yellow]-[/] Have a power switch or kill-cord within reach\n"
                "    [yellow]-[/] Never leave a running robot unattended\n"
                "    [yellow]-[/] Start with low speed/torque settings",
                border_style="yellow",
                title="[bold]Safety First[/]",
            )
        )
    else:
        print(f"{Colors.WARNING}{Colors.BOLD}--- SAFETY WARNING ---{Colors.ENDC}")
        print(f"{Colors.WARNING}")
        print("  OpenCastor controls PHYSICAL MOTORS and SERVOS.")
        print("  Before continuing, please ensure:")
        print()
        print("    - Keep hands and cables clear of moving parts")
        print("    - Have a power switch or kill-cord within reach")
        print("    - Never leave a running robot unattended")
        print("    - Start with low speed/torque settings")
        print(f"{Colors.ENDC}")

    ack = input("  Type 'yes' to acknowledge and continue: ").strip().lower()
    if ack != "yes":
        print(
            f"\n  Setup cancelled.  Re-run with {Colors.BOLD}--accept-risk{Colors.ENDC} "
            "to skip this prompt."
        )
        sys.exit(0)
    print()


WIZARD_STATE_PATH = os.path.expanduser("~/.opencastor/wizard-state.yaml")


def _load_previous_state() -> dict:
    """Load previously saved wizard state for re-run defaults."""
    try:
        if os.path.exists(WIZARD_STATE_PATH):
            with open(WIZARD_STATE_PATH) as f:
                state = yaml.safe_load(f) or {}
            return state
    except Exception:
        pass
    return {}


def _save_wizard_state(state: dict) -> None:
    """Save wizard state for future re-runs."""
    try:
        state_dir = os.path.dirname(WIZARD_STATE_PATH)
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        with open(WIZARD_STATE_PATH, "w") as f:
            yaml.dump(state, f, sort_keys=False)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="OpenCastor Setup Wizard")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="QuickStart mode: project name + API key only",
    )
    parser.add_argument(
        "--accept-risk",
        action="store_true",
        help="Skip the safety acknowledgment prompt",
    )
    args = parser.parse_args()

    print(BANNER)

    if HAS_RICH:
        _console.print(f"[bold magenta]OpenCastor Setup Wizard v{__version__}[/]")
        _console.print("Generating spec compliant with [bold]rcan.dev/spec[/]\n")
    else:
        print(f"{Colors.HEADER}OpenCastor Setup Wizard v{__version__}{Colors.ENDC}")
        print(f"Generating spec compliant with {Colors.BOLD}rcan.dev/spec{Colors.ENDC}\n")

    # --- Safety Acknowledgment ---
    _safety_acknowledgment(args.accept_risk)

    # --- QuickStart vs Advanced ---
    quickstart = args.simple
    if not quickstart:
        print(f"{Colors.GREEN}--- SETUP MODE ---{Colors.ENDC}")
        print("  [1] QuickStart  (project name + API key, sensible defaults)")
        print("  [2] Advanced    (full hardware, channel, and driver config)")
        mode = input_default("Selection", "1")
        quickstart = mode != "2"
        print()

    # --- Load previous state for defaults ---
    prev = _load_previous_state()
    if prev:
        print(
            f"  {Colors.GREEN}[RECALL]{Colors.ENDC} Found previous config — values shown as defaults."
        )

    # --- Step 1: Project Name ---
    robot_name = input_default("Project Name", prev.get("robot_name", "MyRobot"))

    if quickstart:
        # -- QuickStart: New multi-step flow --
        already_authed = set()

        # Step 2: Provider
        provider_key = choose_provider_step(default=prev.get("provider"))

        # Step 3: Authentication
        authenticate_provider(provider_key, already_authed=already_authed)

        # Step 4: Primary model
        model_info = choose_model(provider_key)
        agent_config = _build_agent_config(provider_key, model_info)

        # Step 5: Secondary models
        secondary_models = choose_secondary_models(provider_key, already_authed)

        # Step 6: Brain Architecture
        tiered_config = choose_brain_architecture(provider_key, secondary_models, already_authed)

        # Step 7: Messaging channel (optional)
        print(f"\n{Colors.GREEN}--- MESSAGING (optional) ---{Colors.ENDC}")
        print("  Connect a messaging app to talk to your robot.")
        print("  [0] Skip for now")
        print("  [1] WhatsApp (scan QR code — no account needed!)")
        print("  [2] Telegram Bot")
        ch_choice = input_default("Selection", "0").strip()
        selected_channels = []
        if ch_choice == "1":
            selected_channels = [CHANNELS["1"]]
        elif ch_choice == "2":
            selected_channels = [CHANNELS["3"]]
        if selected_channels:
            collect_channel_credentials(selected_channels)

        preset = "rpi_rc_car"
        rcan_data = generate_preset_config(
            preset, robot_name, agent_config, secondary_models=secondary_models
        )
        # Merge tiered brain config if selected
        if tiered_config:
            # Apply agent override (fast brain becomes primary)
            if "agent_override" in tiered_config:
                for k, v in tiered_config.pop("agent_override").items():
                    rcan_data.setdefault("agent", {})[k] = v

            # Add planner to secondary models
            if "planner_secondary" in tiered_config:
                planner = tiered_config.pop("planner_secondary")
                rcan_data.setdefault("agent", {}).setdefault("secondary_models", [])
                rcan_data["agent"]["secondary_models"].insert(0, planner)

            # Merge remaining keys (tiered_brain, reactive)
            rcan_data.update(tiered_config)
    else:
        # -- Advanced Path (legacy) --
        agent_config = choose_provider()
        collect_api_key(agent_config)

        preset = choose_hardware()
        if preset is not None:
            rcan_data = generate_preset_config(preset, robot_name, agent_config)
        else:
            links = get_kinematics()
            drivers = get_drivers(links)
            rcan_data = generate_custom_config(robot_name, agent_config, links, drivers)

        selected_channels = choose_channels()
        collect_channel_credentials(selected_channels)
        secondary_models = []

    # --- Auto-generate Gateway Auth Token ---
    if not os.getenv("OPENCASTOR_API_TOKEN"):
        import secrets

        token = secrets.token_hex(24)
        _write_env_var("OPENCASTOR_API_TOKEN", token)
        print(
            f"\n  {Colors.GREEN}[AUTO]{Colors.ENDC} Gateway auth token generated and saved to .env"
        )
        print(f"  {Colors.BOLD}OPENCASTOR_API_TOKEN{Colors.ENDC}={token[:8]}...")

    # --- Save wizard state for future re-runs ---
    _save_wizard_state(
        {
            "robot_name": robot_name,
            "provider": agent_config.get("provider", ""),
            "model": agent_config.get("model", ""),
        }
    )

    # --- Generate Config ---
    filename = f"{robot_name.lower().replace(' ', '_')}.rcan.yaml"

    if HAS_RICH:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=_console,
        ) as progress:
            progress.add_task(description="Writing config file...", total=None)
            with open(filename, "w") as f:
                yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)
    else:
        with open(filename, "w") as f:
            yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)

    # --- Auto-detect RCAN capabilities ---
    try:
        from castor.rcan.capabilities import CapabilityRegistry

        cap_reg = CapabilityRegistry(rcan_data)
        detected_caps = cap_reg.names
        if detected_caps:
            print(f"\n{Colors.HEADER}Detected RCAN Capabilities:{Colors.ENDC}")
            for cap in detected_caps:
                print(f"  {Colors.GREEN}+{Colors.ENDC} {cap}")
    except Exception:
        detected_caps = []

    # --- Post-Wizard Health Check ---
    try:
        from castor.doctor import print_report, run_post_wizard_checks

        if HAS_RICH:
            _console.print("\n[bold magenta]--- Running Health Check ---[/]")
        else:
            print(f"\n{Colors.HEADER}--- Running Health Check ---{Colors.ENDC}")
        results = run_post_wizard_checks(filename, rcan_data, agent_config["provider"])
        print_report(results, colors_class=Colors)
    except Exception:
        pass

    # --- Summary ---
    if HAS_RICH:
        _console.print(f"\n{'=' * 50}")
        _console.print("[bold green]Setup Complete![/]\n")
        _console.print(f"  Config file:  [cyan]{filename}[/]")
        _console.print(f"  AI Provider:  {agent_config['label']}")
        _console.print(f"  Model:        {agent_config['model']}")

        if secondary_models:
            names = ", ".join(sm.get("label", sm["model"]) for sm in secondary_models)
            _console.print(f"  Secondary:    {names}")

        if selected_channels:
            names = ", ".join(ch["label"] for ch in selected_channels)
            _console.print(f"  Channels:     {names}")

        _console.print("\n[bold]Next Steps:[/]")
        _console.print(f"  1. Run the robot:        [cyan]castor run --config {filename}[/]")
        _console.print(f"  2. Start the gateway:    [cyan]castor gateway --config {filename}[/]")
        _console.print("  3. Open the dashboard:   [cyan]castor dashboard[/]")
        _console.print("  4. Check status:         [cyan]castor status[/]")
        _console.print(
            f"  5. Auto-start on boot:   [cyan]castor install-service --config {filename}[/]"
        )
        _console.print(
            f"  6. Test your hardware:   [cyan]castor test-hardware --config {filename}[/]"
        )
        _console.print(f"  7. Calibrate servos:     [cyan]castor calibrate --config {filename}[/]")
        _console.print("\n  Or with Docker:          [cyan]docker compose up[/]")
        _console.print("  Validate config:         https://rcan.dev/spec/")
    else:
        print(f"\n{Colors.BOLD}{'=' * 50}{Colors.ENDC}")
        print(f"{Colors.GREEN}Setup Complete!{Colors.ENDC}\n")
        print(f"  Config file:  {Colors.BLUE}{filename}{Colors.ENDC}")
        print(f"  AI Provider:  {agent_config['label']}")
        print(f"  Model:        {agent_config['model']}")

        if secondary_models:
            names = ", ".join(sm.get("label", sm["model"]) for sm in secondary_models)
            print(f"  Secondary:    {names}")

        if selected_channels:
            names = ", ".join(ch["label"] for ch in selected_channels)
            print(f"  Channels:     {names}")

        print(f"\n{Colors.BOLD}Next Steps:{Colors.ENDC}")
        print(
            f"  1. Run the robot:        {Colors.BLUE}castor run --config {filename}{Colors.ENDC}"
        )
        print(
            f"  2. Start the gateway:    "
            f"{Colors.BLUE}castor gateway --config {filename}{Colors.ENDC}"
        )
        print(f"  3. Open the dashboard:   {Colors.BLUE}castor dashboard{Colors.ENDC}")
        print(f"  4. Check status:         {Colors.BLUE}castor status{Colors.ENDC}")
        print(
            f"  5. Auto-start on boot:   "
            f"{Colors.BLUE}castor install-service --config {filename}{Colors.ENDC}"
        )
        print(
            f"  6. Test your hardware:   "
            f"{Colors.BLUE}castor test-hardware --config {filename}{Colors.ENDC}"
        )
        print(
            f"  7. Calibrate servos:     "
            f"{Colors.BLUE}castor calibrate --config {filename}{Colors.ENDC}"
        )
        print(f"\n  Or with Docker:          {Colors.BLUE}docker compose up{Colors.ENDC}")
        print("\n  Validate config:         https://rcan.dev/spec/")

    # --- Offer to start the robot ---
    print()
    try:
        start = input_default("Start your robot now? (y/n)", "y").strip().lower()
        if start in ("y", "yes"):
            print(f"\n{Colors.GREEN}Starting OpenCastor...{Colors.ENDC}\n")
            import subprocess

            subprocess.run([sys.executable, "-m", "castor.cli", "run", "--config", filename])
    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {Colors.BOLD}To start later:{Colors.ENDC} castor run --config {filename}")


if __name__ == "__main__":
    main()
