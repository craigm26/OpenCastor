# Contributing to OpenCastor

We're building the universal runtime for embodied AI and we need your help.
Whether you're adding a new LLM provider, writing a hardware driver, building a
messaging integration, or fixing a typo -- every contribution counts.

## Quick Start for Contributors

```bash
git clone https://github.com/continuonai/OpenCastor.git
cd OpenCastor
pip install -e ".[channels,dev]"
cp .env.example .env
# Edit .env with your API keys
```

## How to Add a New AI Provider

Providers live in `castor/providers/`. Every provider follows the same pattern:

1. **Create** `castor/providers/<name>_provider.py`
2. **Subclass** `BaseProvider` from `castor/providers/base.py`
3. **Implement** `__init__(self, config)`:
   - Resolve API key: environment variable first, then `config.get("api_key")`
   - Initialize the SDK client
4. **Implement** `think(self, image_bytes: bytes, instruction: str) -> Thought`:
   - Encode the image (base64 for most providers)
   - Call the model with `self.system_prompt` and the instruction
   - Parse the response with `self._clean_json(text)`
   - Return a `Thought(raw_text, action_dict)`
5. **Register** in `castor/providers/__init__.py`:
   - Import your class
   - Add an `elif` branch in `get_provider()`
6. **Add** the SDK to `pyproject.toml` dependencies and `requirements.txt`

Example skeleton:

```python
import os
import logging
from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.MyProvider")

class MyProvider(BaseProvider):
    def __init__(self, config):
        super().__init__(config)
        api_key = os.getenv("MY_PROVIDER_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("MY_PROVIDER_API_KEY not found")
        # Initialize your SDK client here

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        try:
            # Call your model, get response text
            text = "..."
            action = self._clean_json(text)
            return Thought(text, action)
        except Exception as e:
            logger.error(f"MyProvider error: {e}")
            return Thought(f"Error: {e}", None)
```

## How to Add a New Hardware Driver

Drivers live in `castor/drivers/`. Every driver gracefully degrades to mock mode
when hardware libraries are unavailable.

1. **Create** `castor/drivers/<name>.py`
2. **Subclass** `DriverBase` from `castor/drivers/base.py`
3. **Implement** `move()`, `stop()`, `close()`
4. **Use try/except** for SDK imports with a module-level `HAS_<NAME>` boolean
5. **Add** the protocol mapping in `get_driver()` in `castor/main.py`
6. **Add** the SDK to `pyproject.toml` and `requirements.txt`

Key conventions:
- Always provide mock mode when hardware SDK is missing
- Clamp values to safe physical ranges
- Log with `logging.getLogger("OpenCastor.<Name>")`

## How to Add a New Messaging Channel

Channels live in `castor/channels/`. Each channel receives commands from users on
a messaging platform and forwards them to the robot's brain.

1. **Create** `castor/channels/<name>.py`
2. **Subclass** `BaseChannel` from `castor/channels/base.py`
3. **Implement**:
   - `start()` -- connect to the platform (bot login, webhook setup)
   - `stop()` -- disconnect gracefully
   - `send_message(chat_id, text)` -- send a reply back to the user
   - `_on_message(chat_id, text)` callback via the base class
4. **Register** in `castor/channels/__init__.py`
5. **Add** environment variables to `.env.example`
6. **Add** the SDK to `pyproject.toml` optional dependencies

## Code Style

- **PEP 8** with 100-char line length
- **snake_case** for functions and variables
- **Type hints** on public method signatures
- **Docstrings** on classes and non-trivial methods
- **Lazy imports** for optional hardware/channel SDKs (import inside try/except or constructor)
- **Structured logging** with per-module loggers: `logging.getLogger("OpenCastor.<Module>")`

We use [Ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check castor/
ruff format castor/
```

## Testing

```bash
pytest tests/
```

Tests go in the `tests/` directory, mirroring the `castor/` package structure.

## Pull Request Process

1. Fork the repo and create your branch from `main`
2. Add or update tests for your changes
3. Ensure RCAN configs still validate: `python .github/scripts/validate_rcan.py --schema <schema> --dir .`
4. Run the linter: `ruff check castor/`
5. Open a PR with a clear description of what and why

## Areas We Need Help With

- **Driver Adapters**: ODrive, VESC, ROS2 bridges, ESP32 serial
- **New Brains**: Mistral, Grok, Cohere, local vision models
- **Messaging Channels**: Matrix, Signal, Google Chat, iMessage
- **Sim-to-Real**: Gazebo/MuJoCo integration
- **Latency Optimization**: Reducing time-to-first-token for real-time reflex arcs
- **Tests**: Unit tests, integration tests, hardware mock tests

## License

By contributing, you agree that your contributions will be licensed under the
Apache 2.0 License.
