# OpenCastor Makefile — common dev tasks
#
# Usage:
#   make release VERSION=2026.2.20.5   # bump, sync, commit, tag, push
#   make sync-version                  # update all version touchpoints from pyproject.toml
#   make test                          # run full test suite
#   make lint                          # ruff check + format

.PHONY: release sync-version test lint

PYTHON := python3
VERSION_FILE := pyproject.toml

# Read current version from pyproject.toml
CURRENT_VERSION := $(shell grep '^version' $(VERSION_FILE) | sed 's/version = "//;s/"//')

# ─────────────────────────────────────────────
# make release VERSION=2026.2.20.5
# ─────────────────────────────────────────────
release:
	@if [ -z "$(VERSION)" ]; then \
		echo "❌  Usage: make release VERSION=YYYY.M.DD.PATCH"; \
		exit 1; \
	fi
	@echo "🚀  Releasing v$(VERSION)..."
	# 1. Sync all version touchpoints
	$(PYTHON) scripts/sync-version.py $(VERSION)
	# 2. Run tests
	$(PYTHON) -m pytest --tb=short -q
	# 3. Lint
	ruff check castor/ --fix
	ruff format castor/
	# 4. Commit
	git add -A
	git commit -m "🔖 v$(VERSION) release"
	# 5. Tag
	git tag v$(VERSION)
	# 6. Push
	git push origin main --tags
	@echo "✅  v$(VERSION) released!"

# ─────────────────────────────────────────────
# make sync-version  (no version bump, just sync existing)
# ─────────────────────────────────────────────
sync-version:
	@echo "🔄  Syncing v$(CURRENT_VERSION) across all touchpoints..."
	$(PYTHON) scripts/sync-version.py

# ─────────────────────────────────────────────
# make test
# ─────────────────────────────────────────────
test:
	$(PYTHON) -m pytest --tb=short -q

# ─────────────────────────────────────────────
# make lint
# ─────────────────────────────────────────────
lint:
	ruff check castor/ --fix
	ruff format castor/
