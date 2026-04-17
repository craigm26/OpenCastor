# ROBOT.md — Public/Private Strategy & Blog-Post Framing

**Status:** Draft for approval
**Date:** 2026-04-17
**Author:** craigm26 (with Claude Code, Opus 4.7)
**Supersedes:** the implicit strategy in `2026-04-17-robot-md-repo-design.md` (see §6) regarding repo visibility, ownership home, and proposal disclosure.

---

## 1. Why this doc exists

We sat down to write a blog post titled "ROBOT.md and how it fits in the OpenCastor ecosystem." In the process of defining the post's angle, we surfaced three unresolved strategic questions that must be answered *before* the post can be written honestly:

1. **Ownership home.** Should `robot-md` live at `craigm26/`, `continuonai/`, `robotregistryfoundation/`, or elsewhere?
2. **Visibility posture.** Public repo? Private repo? Split public/private planes?
3. **Outreach disclosure.** Is the Anthropic adoption pitch "drafted in public" (as the proposal's own footer currently asserts) or kept discreet?

This document records the facts, the decision, and the execution sequence.

---

## 2. North-star constraint (user-provided)

> **"Stupid easy for every provider to push and adopt."**

This rules more than it appears. "Every provider" means Anthropic, OpenAI, Google, Meta, xAI, and any open-source agent framework — not just Anthropic. "Stupid easy to push" means a provider can ship ROBOT.md support to users in an afternoon, with no dependency on any specific vendor's CLI or SDK. "Stupid easy to adopt" means a robot builder hand-writes one file and is done.

Every strategic choice below is judged against this test.

---

## 3. Facts as of 2026-04-17 ~20:00 UTC

### 3.1 The `robot-md` artifact

- Repository: `craigm26/robot-md`, created today 18:53 UTC, last push 19:55 UTC.
- **Public on GitHub.** 0 stars, 0 forks, 0 issues, 0 PRs, 0 watchers.
- 15 commits. Apache 2.0 licensed in `LICENSE`, `cli/pyproject.toml`, `CONTRIBUTING.md`.
- 243-line format spec (`spec/robot-md-v1.md`) + 218-line JSON Schema (`schema/v1/robot.schema.json`).
- Four worked examples: Bob, minimal, SO-ARM101, Turtlebot 4.
- Working Python CLI (`robot-md validate | render | context`) and a working Claude Code SessionStart hook.
- Documented (not yet coded) Claude Desktop MCP server + Claude Mobile URL-bridge approaches for v0.2.
- `proposal/anthropic-adoption-proposal.md` and `proposal/anthropic-outreach.md` are checked in — the latter contains a fallback/rotation list (Google DeepMind, Figure, Boston Dynamics, Agility, Hugging Face).

### 3.2 External footprint

- **`robotmd.dev` is LIVE** (HTTP 200). Static single-page Cloudflare Pages site, deployed from `craigm26/robot-md`'s `site/` subdir.
- **`pypi.org/project/robot-md`: not yet published.** Name available.
- **Wayback Machine has no snapshot of `craigm26/robot-md`.** `archived_snapshots: {}`. Clean-reset window still open, but finite.

### 3.3 Ownership namespace options

- `robotmd` GitHub: squatted by a dormant 2017 user account. **Unavailable.**
- `robotregistryfoundation` GitHub: does not exist yet. **Available**, but requires web-UI org creation (RRF exists as a domain at `robotregistryfoundation.org`, HTTP 200, but not yet a legal entity; GitHub org creation doesn't depend on legal status).
- `continuonai` GitHub: **exists today**, already runs a mixed public/private pattern (public: `rcan-spec`, `rcan-py`, `rcan-ts`, `continuon-web`; private: `continuonos`, `ContinuonAI`, `Continuon-Cloud`, `continuon-proto`, `continuon-clients`). `continuonai/rcan-spec` is the exact pattern sibling for a spec repo: CC BY 4.0 spec license, Astro site deployed to `rcan.dev`, CI badge, multi-SDK listing. **Zero-friction home.**

### 3.4 The April 1 blog post

- `personalsite/src/content/blog/2026-04-01-robot-md-live-context.md` + its LinkedIn companion `src/content/linkedin/2026-04-01-robot-md.md` both describe the *operational-memory* pattern (autoDream + `robot-memory.md` + `<robot-context>` injection) — **and call that pattern "ROBOT.md."**
- The *new* `ROBOT.md` (this strategy doc's subject) is a *static identity manifest*. Different thing entirely.
- The April 1 post is the **only** file in `personalsite/src/content/` that references `robot-md` or `ROBOT.md`. Zero internal cross-links from other posts. Clean delete, no orphans.
- No redirect config in personalsite; deletion → 404 on next deploy.
- 6 iterative commits on the post over the past two weeks indicate the author was already wrestling with the framing.

### 3.5 Production state on the reference robot

- Bob currently runs from `bob.rcan.yaml`, **not** a `ROBOT.md` file. `robot-md/examples/bob.ROBOT.md` is an *example*, not Bob's live config.
- OpenCastor's codebase has **zero references** to `ROBOT.md`, `robot-md`, or `robot_md` in `castor/`. The runtime does not yet consume the format.
- So: ROBOT.md is a **freshly shipped spec + tool**, not yet in production on any robot. The blog post's tone should reflect "launched today" not "battle-tested."

### 3.6 Tension already baked into the current artifacts

- `proposal/anthropic-outreach.md` contains a fallback list that names competitor targets ("If Anthropic passes ... Google DeepMind ... Figure AI ... Boston Dynamics"). Visible in public git history right now.
- `proposal/anthropic-adoption-proposal.md` closes with: *"This proposal is drafted in public (committed to the robot-md repo) so Anthropic reviewers can inspect the full context."* — meta-framing that **explicitly claims radical transparency** as a feature.
- The user's recent directives ("should be private", "delete the post entirely") point toward a **more discreet** posture that contradicts the proposal's baked-in transparency assumption.

This is the real tension the strategy must resolve.

---

## 4. The philosophical choice — Model A vs Model B

Before choosing an implementation (α/β/γ below), the operating model has to be named.

### Model A — Radical transparency

Everything about ROBOT.md, including the strategy for getting it adopted, lives in the open. Fallback lists, rotation plans, and "if Anthropic passes" language are *features* — they signal seriousness and remove any perception that you're bluffing about your alternatives. This is the model the current docs enshrine.

**When A wins:** when demonstrating commitment is more valuable than preserving negotiating leverage. Typical in standards work where adoption is the goal and governance matters more than deal terms.

### Model B — Strategic discretion

The spec and the adoption strategy are separate concerns. The spec is maximally public because the standard needs it. The outreach strategy is competitive intelligence — not secret, but not for broadcast either. This is the model the user's recent directives gesture toward.

**When B wins:** when preserving the ability to pivot outreach strategy matters, or when naming competitive fallbacks publicly could poison any of those relationships later.

### Asymmetry of future pivots

- **Discreet → Transparent** is trivial: flip a private repo to public, or push the strategic docs to a public location later.
- **Transparent → Discreet** requires history rewriting (`git filter-repo`), accepting that any archive.org / GitHub-Archive / web-cache snapshot is permanent, and losing all signals (stars, etc.) the public repo had accumulated.

**Model B is the lower-regret default for anyone uncertain**, because it preserves optionality. Model A is the right call only when you're confident you want transparency as a *strategy*, not a default.

### Recommendation

**Operate under Model B.** The user's instincts have been steady on this for four messages; the proposal's "drafted in public" framing is one author's moment of conviction that may not survive contact with production adoption dynamics (especially as competitors read the fallback list). Preserving optionality is cheap; reclaiming it isn't.

**Concretely:** the spec, schema, examples, CLI, and Claude Code hook are public; the outreach strategy (`proposal/`) lives private; the meta-framing in the proposal that asserts radical transparency is rewritten to drop that claim.

---

## 5. Implementation — α, β, γ

Three ways to execute Model B (or Model A, if that's chosen):

### Option α — Transparent, transfer as-is

Transfer `craigm26/robot-md` → `continuonai/robot-md` (today) or `robotregistryfoundation/robot-md` (when that org exists) via `gh api transfer`. Keep everything public including `proposal/`. Update the 8 internal hardcoded URLs, reconfigure Cloudflare Pages GitHub integration, publish PyPI.

- **Pros:** one command, reversible, preserves git history as-is, zero risk of broken clones (there are no clones).
- **Cons:** entrenches Model A permanently; leaves `proposal/` fallback list in public history forever.
- **Pairs with:** Model A.

### Option β — Split with clean reset

Use the rare 1-hour-old-repo / no-Wayback-snapshot window to:

1. Rewrite local history via `git filter-repo --path proposal --invert-paths` to remove `proposal/` from all commits.
2. Push cleaned history to `continuonai/robot-md` (public).
3. Push `proposal/` (with original history) to `continuonai/robot-md-private` (private).
4. **Delete `craigm26/robot-md`** on GitHub (point of no return).
5. Reconfigure Cloudflare Pages to watch the new public repo.
6. Update hardcoded URLs in the new public repo to point at the new namespace.
7. Rewrite the proposal's own meta-framing to drop the "drafted in public" assertion.

- **Pros:** clean public history from day one; preserves the clean-reset window before archive.org catches up; honest Model B posture; `proposal/` still version-controlled just not publicly.
- **Cons:** not reversible after step 4; requires `git filter-repo` installed and careful execution; burns the 15-commit public history for a 1-hour-old repo (low loss).
- **Pairs with:** Model B.

### Option γ — All private under a neutral org

Transfer to `continuonai/robot-md` *and* flip to private. Serve the spec publicly only via `robotmd.dev` as HTML; GitHub has no public repo.

- **Pros:** maximum discretion; spec still adopts via web URL.
- **Cons:** blocks provider adoption — every agent framework wants to inspect source, schema, CLI. Contradicts the north-star "stupid easy to adopt" test. Forces operators to copy/paste the spec HTML into their own files instead of pip-installing.
- **Pairs with:** an extreme form of Model B that is too extreme for the stated goal.

### Recommendation

**Option β, with `continuonai/robot-md` as the public home today.** Transfer to `robotregistryfoundation/robot-md` later when that GitHub org is created (it's a trivial second transfer). `continuonai` is the zero-friction, pattern-match home right now (`rcan-spec` is the exact template).

---

## 6. Per-artifact posture matrix (Model B + Option β)

| # | Artifact | Public home (`continuonai/robot-md`) | Private home (`continuonai/robot-md-private`) | Rationale |
|---|---|---|---|---|
| 1 | `spec/robot-md-v1.md` | ✅ | | The standard. Must be public. |
| 2 | `schema/v1/robot.schema.json` | ✅ | | Machine-readable contract. Public for validators. |
| 3 | `examples/*.ROBOT.md` | ✅ | | Adoption accelerant. |
| 4 | `cli/` (Python CLI) | ✅ | | Convenience, not gatekeeping. Public accelerates adoption. |
| 5 | `integrations/claude-code/` | ✅ | | The SessionStart hook is 15 lines of bash; too trivial to hide. |
| 6 | `integrations/claude-desktop/README.md` | ✅ | | Documented pattern. MCP server code itself (v0.2) also public since the outreach already promises `pip install robot-md-mcp`. |
| 7 | `integrations/claude-mobile/README.md` | ✅ | | Same as above. |
| 8 | `site/` (robotmd.dev landing) | ✅ | | Public by definition. |
| 9 | `ROBOT.md` (dogfood) | ✅ | | The self-declaration. |
| 10 | `README.md`, `LICENSE`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` | ✅ | | Standard open-source surface. |
| 11 | `.github/workflows/` | ✅ | | CI transparency. PyPI + Cloudflare secrets live as repo secrets (never in files). |
| 12 | `proposal/anthropic-adoption-proposal.md` | | ✅ | Contains strategic positioning. |
| 13 | `proposal/anthropic-outreach.md` | | ✅ | Contains fallback/rotation list. Competitive intelligence. |
| 14 | Future internal strategic docs | | ✅ | Default home for anything outreach-adjacent going forward. |

---

## 7. Migration sequence

Every step before step 6 is reversible. Step 6 burns `craigm26/robot-md` and is the point of no return.

### Preconditions (user actions, blocking)

- **[user]** `pip install git-filter-repo` on local machine (or confirm availability).
- **[user]** Confirm `continuonai` is the intended public home (vs. waiting for RRF org creation).
- **[user]** Confirm April 1 blog post + LinkedIn companion are deleted (not retitled, not drafted).

### Execution (assistant-drivable)

1. **Belt-and-braces backup.** `cp -r /home/craigm26/robot-md /home/craigm26/robot-md.backup.2026-04-17/` — untouched snapshot. Free rollback.
2. **Create two fresh repos** under `continuonai` via `gh repo create`:
   - `continuonai/robot-md` (public, no README — we'll push existing).
   - `continuonai/robot-md-private` (private, no README).
3. **In a working clone**, run `git filter-repo --path proposal --invert-paths` to strip `proposal/` from all history. Verify with `git log -- proposal/` (should return nothing) and `git show HEAD -- proposal/` (nothing).
4. **Update hardcoded URLs** in the filtered clone (8 files):
   - `cli/pyproject.toml`: `Repository`, `Issues` URLs.
   - `README.md`, `CONTRIBUTING.md`, `docs/pypi-publish.md`, `site/README.md`, `site/index.html`, `wrangler.toml` — swap `craigm26/robot-md` → `continuonai/robot-md`.
5. **Push filtered clone** to `continuonai/robot-md` (force-push to a clean repo; no conflicts possible).
6. **Push `proposal/`** with original history to `continuonai/robot-md-private`:
   - Fresh clone from the backup (step 1).
   - `git filter-repo --path proposal --path LICENSE` — keep only the `proposal/` tree and `LICENSE` file, discarding everything else from history.
   - Add a new `README.md` in the private repo explaining what it is and pointing at the public repo.
   - Push to `continuonai/robot-md-private`.
7. **Rewrite `proposal/anthropic-adoption-proposal.md`** in the private repo to drop the "drafted in public" closing paragraph. Update any repo-URL references accordingly.
8. **Delete `craigm26/robot-md`** via `gh repo delete craigm26/robot-md --yes`. **Point of no return.**
9. **Reconfigure Cloudflare Pages** — login to dashboard, disconnect `robotmd-dev` from `craigm26/robot-md`, reconnect to `continuonai/robot-md`. Verify deploy succeeds on next push.
10. **Delete April 1 blog + LinkedIn** in a single commit in `personalsite`. Don't push yet — user drives the deploy.
11. **Rewrite 3 OpenCastor design docs** (`2026-04-17-robot-md-repo-design.md`, `2026-04-17-robot-md-v0.1-implementation.md`, `2026-04-17-robot-md-reactive-layer.md`) to reference the new URL — or leave as historical. Recommendation: add a single header note to each pointing at this strategy doc; don't rewrite bodies (history is fine).
12. **Blog-post spec** (separate doc): lands at `docs/superpowers/specs/2026-04-17-robot-md-blog-post-design.md` once steps 1–10 are done.

### Rollback posture per step

| If failure at step | Recovery |
|---|---|
| 1 | Nothing happened. No-op. |
| 2 | `gh repo delete continuonai/robot-md` / `continuonai/robot-md-private`. Craigm26's public repo untouched. |
| 3 | Working clone is disposable. `rm -rf` and restart. |
| 4 | Working clone is disposable. |
| 5 | Force-push again with fix. No external consumers. |
| 6 | Force-push to private repo. |
| 7 | Edit a commit; force-push private. |
| 8 | **IRREVERSIBLE.** Before executing: verify step 5 succeeded AND `continuonai/robot-md` is correctly serving. |
| 9 | Cloudflare UI; revert to pointing at a backup repo we can push to if needed. |
| 10 | `git reset HEAD~1` in personalsite. |

---

## 8. Blog-post implications (derived from this strategy)

Once the strategy above is executed, the blog post has a clean runway:

- **Title candidate:** *"ROBOT.md: A Session-Start File for Robots"* — or *"The CLAUDE.md of Physical Robots"* — or some variant that leverages the Claude Code SessionStart framing the user called out explicitly.
- **Three contrasts the post can cleanly draw:**
  1. **ROBOT.md vs. OpenCastor** — one is *what the robot is* (public standard, vendor-neutral, stewarded by ContinuonAI today); the other is *what the robot runs* (open-source workshop, harness optimizer, leaderboards, experimentation).
  2. **ROBOT.md vs. RCAN** — one is a robot describing itself; the other is robots talking to each other.
  3. **ROBOT.md vs. CLAUDE.md** — the obvious parallel the whole design leans on.
- **Tagline:** *"ROBOT.md is to your robot what CLAUDE.md is to your codebase. Every agent can read it. Nobody owns it."*
- **SessionStart framing:** anchor on the concrete adoption primitive — *every agent harness that supports session-start hooks can support ROBOT.md in an afternoon.* Claude Code already does via the v0.1 `session-start.sh` hook. ChatGPT custom GPTs, Gemini system instructions, and Ollama modelfiles are all plausible hooks.
- **Honesty note:** because ROBOT.md is launching today and no robot is yet running off one (Bob runs `.rcan.yaml`), the post is a *launch announcement*, not a retrospective. Tone: "here's what we built today, here's what comes next," not "here's what we've learned from production."
- **No mention of outreach strategy, fallback providers, or Anthropic adoption specifics.** Those stay in the private repo.

---

## 9. Open questions for the user

These gate execution. Answering them unblocks the migration.

1. **Confirm Model B + Option β is the direction.** (Alternatives: Model A + Option α; Model B + Option γ.)
2. **Confirm `continuonai/robot-md` as the public home today** — with later transfer to `robotregistryfoundation/robot-md` when that org exists. (Alternative: create RRF org now before any migration.)
3. **Confirm `git-filter-repo` is available** (or authorize `pip install git-filter-repo`).
4. **Confirm April 1 blog post + LinkedIn companion: delete entirely** — not retitle, not draft. (User said "yes, delete the post entirely" in response to the blog-only question; implicit for the LinkedIn companion since the collision is identical, but should be confirmed.)
5. **The deletion → 404 window.** Accept the 404 for `craigmerry.com/blog/2026-04-01-robot-md-live-context/` until the new post lands, at which point I add an Astro redirect? Or set up the redirect at deletion time pointing at a placeholder landing on the new post's future URL? Recommendation: accept the 404; add redirect once the new post is live.

---

## 10. What this doc replaces

- Any implicit strategy in `2026-04-17-robot-md-repo-design.md` regarding repo visibility (that doc's "private at creation, flip to public later" line is superseded by Option β + Model B here).
- Any implicit assumption in `proposal/anthropic-adoption-proposal.md` or `proposal/anthropic-outreach.md` that the proposal lives in a public repo (those docs move to the private plane; the "drafted in public" framing is rewritten).
- Nothing in the ROBOT.md v1 spec itself — the format is unaffected.

---

## 11. Next step after approval

Invoke `superpowers:writing-plans` to produce the step-by-step implementation plan for §7 (migration sequence). Each step in that plan gets checkboxes, verification, and rollback notes. Then execute via subagent-driven development or the user driving it manually.

After migration, a second writing-plans pass produces `docs/superpowers/specs/2026-04-17-robot-md-blog-post-design.md` — the blog post itself.
