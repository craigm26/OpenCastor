# Contributing to OpenCastor

We're building the universal runtime for embodied AI and we want your help.
Whether you're adding a new LLM provider, writing a hardware driver, building a
messaging integration, or fixing a typo — every contribution counts.

**Join the community:**
- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq) — ask questions, share builds, get help
- **GitHub Issues**: Report bugs or request features
- **Twitter/X**: [@opencastor](https://twitter.com/opencastor)
- **PyPI**: [pypi.org/project/opencastor](https://pypi.org/project/opencastor/)
- **Community Hub**: [opencastor.com/hub](https://opencastor.com/hub) — browse and share robot recipes

## Ways to Contribute

### 🤖 Share a Robot Recipe
The fastest way to contribute. A recipe is a RCAN config + optional scripts for a specific robot:

```bash
castor hub share --submit
```

See `community-recipes/` for examples. Recipes are submitted as GitHub Pull Requests.

### 🐛 Report a Bug
Open an issue: https://github.com/craigm26/OpenCastor/issues

Include your OS, hardware, OpenCastor version (`castor --version`), and the full error output.

### 🔌 Build a Provider
Add support for a new AI provider in `castor/providers/`. See existing providers for the interface. It's a clean 50-line pattern.

### 🧪 Improve Tests
We target high coverage. Pick any area with < 80% coverage and add tests. Run:

```bash
pytest tests/ --cov=castor --cov-report=term-missing
```

### 📚 Improve Docs
Docs live in `site/` (HTML) and `README.md`. Community recipe docs are in `docs/community-recipes.md`.

## Quick Start for Contributors

```bash
git clone https://github.com/craigm26/OpenCastor.git
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

## CLI Commands Reference

OpenCastor provides 41 commands via `castor <command>`. Before adding a new
command, familiarize yourself with the existing ones:

| Group | Commands |
|-------|----------|
| **Setup** | `wizard`, `quickstart`, `configure`, `install-service`, `learn` |
| **Run** | `run`, `gateway`, `dashboard`, `demo`, `shell`, `repl` |
| **Diagnostics** | `doctor`, `fix`, `status`, `logs`, `lint`, `benchmark`, `test` |
| **Hardware** | `test-hardware`, `calibrate`, `record`, `replay`, `watch` |
| **Config** | `migrate`, `backup`, `restore`, `export`, `diff`, `profile` |
| **Safety** | `approvals`, `privacy`, `audit` |
| **Network** | `discover`, `fleet`, `network`, `schedule`, `token` |
| **Advanced** | `search`, `plugins`, `plugin`, `upgrade`, `update-check` |

### How to Add a New CLI Command

1. **Create** a handler function `cmd_<name>(args) -> None` in `castor/cli.py`
2. **Add** a subparser in `main()` with help text and epilog example
3. **Register** in the `commands` dict: `"<name>": cmd_<name>`
4. **Lazy-import** the implementation module inside the handler (keeps startup fast)
5. **Add** the command to the group table above and to `CHANGELOG.md`
6. **Write tests** in `tests/test_cli.py`

## Writing Plugins

Plugins extend OpenCastor with custom commands, drivers, providers, and hooks
without modifying the core codebase.

### Plugin Manifest (`plugin.json`)

Every plugin **must** ship a `plugin.json` manifest alongside the `.py` file.
This is a security requirement — plugins without a manifest are silently skipped
at load time.

```json
{
    "name": "my_plugin",
    "version": "1.0.0",
    "author": "Your Name",
    "hooks": ["on_startup"],
    "commands": ["my-cmd"],
    "sha256": "<hex SHA-256 digest of my_plugin.py>"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | ✅ | Plugin identifier (must match the `.py` filename stem) |
| `version` | ✅ | Semver string, e.g. `"1.0.0"` |
| `author` | ✅ | Plugin author name or contact |
| `hooks` | ✅ | List of hook events registered (may be empty `[]`) |
| `commands` | ✅ | List of CLI command names registered (may be empty `[]`) |
| `sha256` | optional | SHA-256 hex digest of the `.py` file for integrity verification |

Compute the SHA-256 digest to include in your manifest:

```bash
python -c "import hashlib; print(hashlib.sha256(open('my_plugin.py','rb').read()).hexdigest())"
```

### Plugin File Format

```python
# my_plugin.py

def register(registry):
    registry.add_command("my-cmd", my_handler, help="My custom command")
    registry.add_hook("on_startup", my_startup_fn)

def my_handler(args):
    print("Hello from my plugin!")

def my_startup_fn(config):
    print("Robot booting up!")
```

### Installing a Plugin

Use `castor plugin install` to fetch a plugin and record provenance in
`~/.opencastor/plugins.lock`:

```bash
# From a URL (fetches both .py and .json manifest automatically)
castor plugin install https://example.com/my_plugin.py

# From a local path
castor plugin install /path/to/my_plugin.py
```

The installer:
1. Downloads/copies the `.py` file **and** the `plugin.json` manifest
2. Validates the manifest (required fields + optional SHA-256 check)
3. Writes both files to `~/.opencastor/plugins/`
4. Records `source`, `installed_at`, and `sha256` in `~/.opencastor/plugins.lock`

Plugins placed directly in `~/.opencastor/plugins/` without using
`castor plugin install` must still have a valid `plugin.json` manifest or they
will be skipped with a warning.

### Listing Plugins

```bash
castor plugins
```

Shows each plugin's load status, manifest presence, version, and install source.

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
