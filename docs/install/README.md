# Install & Setup

- [Upgrade Guide](upgrade.md) — upgrading between versions, Pi OS venv setup, systemd migration

## Reproducible Installs

For a fully pinned, reproducible install matching the tested environment:

```bash
pip install -r requirements.lock
```

`requirements.lock` is generated from the active development venv and pinned to exact versions. Regenerate with:

```bash
pip freeze | grep -v "^-e" > requirements.lock
```
