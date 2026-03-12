# OpenCastor Security Red Team Analysis Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File a GitHub issue for each confirmed security finding in the OpenCastor repository.

**Architecture:** Read-only code analysis across all scope areas, then file one GitHub issue per distinct finding with severity labels, affected file/line, and recommended fix.

**Tech Stack:** `gh` CLI for issue creation, bash grep/read for code analysis.

---

## Confirmed Findings

### CRITICAL

| # | Finding | File | Lines |
|---|---------|------|-------|
| C1 | Real API tokens committed to `config/swarm.yaml` (git-tracked) | `config/swarm.yaml` | 18, 26 |

### HIGH

| # | Finding | File | Lines |
|---|---------|------|-------|
| H1 | All `/setup/api/*` endpoints are completely unauthenticated — anyone on port 8000 can write RCAN configs and `.env` API keys to disk | `castor/api.py` | 5467–5820 |
| H2 | `/setup/api/save-config` writes arbitrary RCAN YAML and env vars (API keys) to disk with no auth | `castor/api.py` | 5677–5700, `castor/setup_service.py` | 1093–1130 |
| H3 | Firmware downloaded from GitHub API without checksum/signature verification before DFU flash | `castor/cli.py` | 2133–2182 |
| H4 | `/api/metrics` is unauthenticated — exposes provider names, error rates, loop latency | `castor/api.py` | 790 |

### MEDIUM

| # | Finding | File | Lines |
|---|---------|------|-------|
| M1 | CORS defaults to wildcard (`*`) with no production guidance | `castor/api.py` | 87–94 |
| M2 | `?token=` query param for streaming/WS endpoints — tokens appear in access logs & referrer headers | `castor/api.py` | 1255, 1814, 2556, 3084 |
| M3 | Non-constant-time API token comparison in `verify_token()` — `auth != f"Bearer {API_TOKEN}"` | `castor/api.py` | 281 |
| M4 | GitHub Actions `gate`/`test` jobs use mutable version tags, not commit-SHA pins | `.github/workflows/ci.yml`, `release.yml` | — |
| M5 | `StrictHostKeyChecking=no` in deploy SSH commands — MITM risk | `castor/commands/deploy.py` | 190 |
| M6 | `/webhooks/teams` and `/webhooks/matrix` lack HMAC signature verification and rate limiting | `castor/api.py` | 5817–5843 |

### LOW

| # | Finding | File | Lines |
|---|---------|------|-------|
| L1 | No dependency lockfile (pip.lock/requirements.txt pinned) — wide version ranges | `pyproject.toml` | all deps |
| L2 | `/health` endpoint discloses internal state (brain loaded, driver type, channel names) unauthenticated | `castor/api.py` | 338–350 |
| L3 | `/api/behavior/status` intentionally unauthenticated — leaks behavior job names | `castor/api.py` | 2344 |

---

## Chunk 1: Pre-flight + Critical Issues

- [ ] Create `security` label on GitHub
- [ ] File C1: Tokens in swarm.yaml [Critical]
- [ ] File H1: Unauthenticated setup API [High]
- [ ] File H2: Unauthenticated config/env write [High]
- [ ] File H3: Firmware no checksum [High]
- [ ] File H4: Unauthenticated /api/metrics [High]

## Chunk 2: Medium Issues

- [ ] File M1: CORS wildcard default [Medium]
- [ ] File M2: Token in query params [Medium]
- [ ] File M3: Non-constant-time token comparison [Medium]
- [ ] File M4: GitHub Actions unpinned [Medium]
- [ ] File M5: StrictHostKeyChecking=no [Medium]
- [ ] File M6: Teams/Matrix webhooks no HMAC [Medium]

## Chunk 3: Low / Informational Issues

- [ ] File L1: No lockfile [Low]
- [ ] File L2: /health info disclosure [Low]
- [ ] File L3: /api/behavior/status unauthenticated [Low/Informational]
