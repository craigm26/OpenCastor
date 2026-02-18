# Contributing Recipes to the OpenCastor Community Hub

Thank you for sharing your working robot config! Every recipe helps someone skip the hard parts.

## What Is a Recipe?

A recipe is a complete, tested robot configuration that others can install with one command:

```bash
castor hub install <recipe-id>
```

Each recipe lives in `community-recipes/<recipe-id>/` and contains:

| File | Required | Description |
|------|----------|-------------|
| `recipe.json` | ✅ | Metadata: name, description, hardware, AI provider, tags |
| `config.rcan.yaml` | ✅ | The actual RCAN config — must be valid YAML |
| `README.md` | ✅ | Overview, hardware list, setup steps, lessons learned |
| Additional docs | Optional | Build notes, wiring diagrams, STL files, etc. |

## Recipe ID Format

Use the format: `short-name-hexsuffix` (e.g., `picar-home-patrol-e7f3a1`).

Generate a suffix: `python3 -c "import secrets; print(secrets.token_hex(3))"`

## Creating a Recipe

### 1. recipe.json

```json
{
  "id": "my-robot-a1b2c3",
  "name": "My Robot Name",
  "description": "One-sentence description of what it does",
  "author": "your_handle",
  "category": "home|agriculture|industrial|education|research|outdoor|service",
  "difficulty": "beginner|intermediate|advanced",
  "hardware": ["List", "of", "hardware"],
  "ai": {
    "provider": "anthropic|openai|google|huggingface|ollama",
    "model": "model-name"
  },
  "tags": ["relevant", "tags"],
  "budget": "$XX",
  "use_case": "What problem does this solve? Be specific and honest.",
  "created": "2026-01-01T00:00:00Z",
  "version": "1.0.0",
  "opencastor_version": "2026.2.17.7",
  "files": {
    "config": "config.rcan.yaml",
    "readme": "README.md",
    "docs": []
  }
}
```

### 2. config.rcan.yaml

A complete, valid RCAN configuration file. Requirements:

- **Must be valid YAML** — test with `python3 -c "import yaml; yaml.safe_load(open('config.rcan.yaml'))"`
- **No API keys or secrets** — use environment variable references or redact
- **No PII** — no real emails, phone numbers, or addresses
- **Realistic values** — actual speeds, pin numbers, and settings you tested
- **Commented** — explain non-obvious choices

### 3. README.md

Write it like you're helping a friend build the same thing:

- **Overview** — what it does, category, difficulty, budget
- **Use case** — why you built it, what problem it solves
- **Hardware list** — with approximate prices and where to buy
- **What works well** — your wins
- **What you'd change** — honest lessons learned
- **Setup instructions** — how to get it running
- **Tips and gotchas** — things that aren't obvious

## How to Submit

### Option A: CLI (recommended)

```bash
# Auto-packages your config with PII scrubbing
castor hub share --config robot.rcan.yaml --docs BUILD_NOTES.md

# Or submit an existing recipe directory
castor hub share --submit community-recipes/my-robot-a1b2c3/
```

### Option B: GitHub Pull Request

1. Fork [craigm26/OpenCastor](https://github.com/craigm26/OpenCastor)
2. Create your recipe directory in `community-recipes/`
3. Add `recipe.json`, `config.rcan.yaml`, and `README.md`
4. Update `community-recipes/index.json` with your recipe entry
5. Open a PR with the title: `recipe: <your-recipe-name>`

### Option C: GitHub Issue

Use the **"New Recipe Submission"** issue template to describe your build. A maintainer will help format it.

## Review Criteria

Recipes are reviewed for:

1. **Tested on real hardware** — not theoretical configs
2. **Valid YAML** — config must parse without errors
3. **No secrets or PII** — API keys, emails, real addresses must be scrubbed
4. **Honest documentation** — failures and limitations are valuable
5. **Complete metadata** — recipe.json has all required fields
6. **Reasonable scope** — solves a real problem someone else might have

We don't reject recipes for being simple! A well-documented beginner recipe is more valuable than a complex one with poor docs.

## License

All recipes submitted to the Community Hub are licensed under **Apache 2.0**, consistent with the OpenCastor project license. By submitting a recipe, you agree to this license.

## Questions?

- Open an issue on GitHub
- Join our [Discord](https://discord.gg/jMjA8B26Bq)
- Email: hello@opencastor.com
