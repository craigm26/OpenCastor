# OpenCastor PyPI versioning & the CalVer trap

**Status:** mitigated by pinning (shipped, credential-free); durable yank deferred
to an operator with PyPI maintainer access.
**Date:** 2026-07-16 · **Owner:** release operator

## The trap

OpenCastor shipped ~98 releases on a **CalVer** scheme (`YYYY.M.D.patch`, e.g.
`2026.4.23.0`) before moving to **SemVer** on the `3.x` line (`3.0.0`, `3.0.1`,
`3.0.2`). Under [PEP 440](https://peps.python.org/pep-0440/) the CalVer line sorts
**above** the 3.x line:

```
Version("2026.4.23.0") > Version("3.0.2")   # True
Version("2026.4.23.0") > Version("3.0.3")   # True  ← a NEW 3.0.3 still loses
```

So `pip install opencastor` (no pin) resolves the stale `2026.4.23.0`, and simply
**publishing a higher 3.x number does not help** — every `2026.*` release outranks
every `3.x` release. Confirmed against the live index on 2026-07-16:

| Command (clean venv, `--no-deps`)            | Resolves to                          |
|----------------------------------------------|--------------------------------------|
| `pip download opencastor`                    | `opencastor-2026.4.23.0-py3-none-any.whl` ← **trap** |
| `pip download "opencastor==3.*"`             | `opencastor-3.0.2-py3-none-any.whl`  ← correct |

PyPI's own "latest" (`info.version` in the JSON API) is likewise `2026.4.23.0`.
Totals: **101 releases; 98 CalVer; 3 on the 3.x line** (`3.0.0/3.0.1/3.0.2`).

## Decision

Two independent fixes; we apply BOTH.

### 1. Pin everywhere — the sole supported install command (shipped now)

The **only** supported install command is pinned to the 3.x line:

```bash
pip install "opencastor==3.*"
```

A bare `pip install opencastor` is **not supported** until the yank (below) lands —
it resolves the retired CalVer line. Every install reference in this repo, the
website, and the iOS app's Set-Up screen is pinned to `opencastor==3.*` (or an
exact `opencastor==3.0.x`). This needs no PyPI credentials and takes effect
immediately for anyone following the docs.

### 2. Yank the 98 CalVer releases — the durable fix (operator, needs PyPI creds)

Yanking ([PEP 592](https://peps.python.org/pep-0592/)) marks the CalVer releases so
pip's resolver stops offering them for a bare/range requirement, while leaving them
installable by exact pin (so nothing already pinned to a `2026.*` version breaks).
After yanking all 98, a bare `pip install opencastor` resolves `3.0.2` — the highest
non-yanked release — and the trap is gone for good.

**Why this is deferred:** Warehouse (pypi.org) exposes **no** token-authenticated
API for yanking — `twine` cannot yank. It is a maintainer **web-UI** action (2FA
login), so it cannot run headless from CI or this workspace. The list and the
procedure are prepared below; a human with maintainer access executes it.

#### Operator procedure

```bash
# 1. Enumerate the versions and their per-release manage URLs:
python scripts/pypi_yank_calver.py            # the 98 versions
python scripts/pypi_yank_calver.py --urls     # https://pypi.org/manage/project/opencastor/release/<v>/

# 2. For EACH version, on its manage page (logged in as a maintainer):
#    Options -> Yank -> reason: "CalVer line superseded by 3.x SemVer;
#    see docs/pypi-versioning.md" -> confirm.

# 3. Verify a bare install now resolves 3.x:
python scripts/pypi_yank_calver.py --verify
# -> expect opencastor-3.0.2-* (NOT 2026.4.x)
```

The full 98-version list lives in `scripts/pypi_yank_calver.py` (`CALVER_VERSIONS`),
generated from `https://pypi.org/pypi/opencastor/json`. Regenerate before yanking if
more CalVer releases were cut in the interim.

#### Do NOT

- **Do not delete** the releases (deletion is irreversible and breaks exact pins;
  yank is the reversible, PEP-592-correct action).
- **Do not** cut another CalVer release. All future releases stay on SemVer `3.x+`.

## After the yank

Once yanked, the pin is still the recommended hygiene (reproducible installs), but a
bare `pip install opencastor` will be safe again. Update the "Status" line at the top
of this file to record the completion date and who performed it.
