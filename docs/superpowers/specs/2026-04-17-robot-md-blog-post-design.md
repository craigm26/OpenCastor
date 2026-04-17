# Blog Post — "ROBOT.md and how it fits in the OpenCastor ecosystem"

**Status:** Draft for approval
**Date:** 2026-04-17
**Author:** craigm26 (with Claude Code, Opus 4.7)
**Depends on:** [`2026-04-17-robot-md-strategy.md`](./2026-04-17-robot-md-strategy.md) — migration to `RobotRegistryFoundation/robot-md` (public) + `RobotRegistryFoundation/robot-md-private` (outreach). Strategy must be executed (Cloudflare deploy green; old repo deleted) **before this post goes live.**

---

## 1. Scope

A launch post for `ROBOT.md` on `craigmerry.com/blog`, written to three audiences at once:

1. **Robotics builders** — what the file is, why it matters, how to adopt in 60 seconds.
2. **Agent-harness authors (Anthropic, OpenAI, Google, Ollama, OSS frameworks)** — the SessionStart-hook primitive so they can ship ROBOT.md support in an afternoon.
3. **Ecosystem observers** — where ROBOT.md sits alongside OpenCastor, RCAN, and RRF.

The post is a **launch announcement**, not a retrospective. No robot is yet running *from* a ROBOT.md in production (Bob runs `bob.rcan.yaml`); the story is "shipped today, here's the primitive, here's the call to action."

---

## 2. Working title

**Primary:** *"ROBOT.md: the session-start file for every agent that drives a robot."*

Alternates (pick one if primary feels off):
- *"A CLAUDE.md for physical robots: introducing ROBOT.md."*
- *"One file, any agent: ROBOT.md and the session-start primitive for robotics."*
- *"Before the agent moves the robot, it should know the robot. That's ROBOT.md."*

Subtitle candidate: *"The 60-second adoption path for any provider — Claude Code, ChatGPT, Gemini, Ollama, or your own harness."*

---

## 3. Thesis

> **Every agent harness already supports session-start context injection. `ROBOT.md` is the file format that turns that primitive into a universal adapter between any planning agent and any robot — with zero dependency on any single vendor's runtime.**

That's the one-sentence version. The post unpacks it by showing:

1. What the file is (YAML frontmatter + markdown prose — one file).
2. What "session start" means concretely in each agent harness (Claude Code hook, ChatGPT custom-GPT instructions, Gemini system instructions, Ollama modelfiles).
3. How it composes with OpenCastor (open runtime), RCAN (wire protocol), and RRF (neutral registry) without requiring any of them.
4. Why the format is maximally public and vendor-neutral (stewarded by ContinuonAI today; path to RRF when that org exists on GitHub).

---

## 4. Structure (9 sections, ~1,400–1,800 words)

Each section gets a target word count so the whole piece stays scannable.

### §1 — The hook (~120 words)

Open with the Claude Code leak insight from last month: *Claude Code loads `CLAUDE.md` + git state at session start. The LLM starts warm. That's the whole trick.* One paragraph.

Then the jump: **what is the robotics equivalent?** Not AGENTS.md. Not URDF. Not the ROS parameter server. **A single file at the robot's root — declarative, schema-validated, one read at session start. We shipped it today. It's called `ROBOT.md`.**

Single-sentence closer: *"If you've ever written `CLAUDE.md`, you already know how to write `ROBOT.md`."*

### §2 — What's in one (~220 words)

Show the minimal example from `examples/minimal.ROBOT.md` — frontmatter + body, 15 lines. Annotate briefly:
- Frontmatter → machine-readable, JSON-Schema-validated.
- Body → human/LLM-readable, what the prose is for.

Then show a more substantial example: Bob's ROBOT.md. Abbreviated, with the three blocks that matter most — `physics`, `capabilities`, `safety`. One line per: *"This is identity, capability, and safety envelope. Everything else is optional."*

Close §2 with: *"That's it. One file. The whole declaration."*

### §3 — Why session-start is the right primitive (~200 words)

Make the universal-adoption argument concrete:

- **Claude Code** — `SessionStart` hook in `~/.claude/settings.json`. Shell script emits stdout → session context. **Shipping in `robot-md` v0.1 today.** Show the 15-line `session-start.sh`.
- **ChatGPT custom GPTs** — instructions field. Paste the body of `ROBOT.md` in. Three clicks.
- **Gemini (Google AI Studio / Vertex)** — system instructions. Same paste model.
- **Ollama** — `SYSTEM` directive in a modelfile. Same.
- **LangChain / AutoGen / CrewAI / Letta** — system-prompt slot. Same.

Tagline: *"Every agent harness worth shipping already has a session-start slot. ROBOT.md fills it with the robot."*

The point isn't that any of these need new SDKs. They don't. ROBOT.md is what they read, not what they depend on.

### §4 — How it fits OpenCastor (~220 words)

**The contrast the post has to draw.** Two layers, clearly distinct:

| | **OpenCastor** | **ROBOT.md** |
|---|---|---|
| What it is | Open runtime — the *workshop* | Open spec — the *passport* |
| Who it's for | Robot builders who want a harness they can modify, run leaderboards on, experiment with models in | Any agent (Claude Code, ChatGPT, Gemini, OSS frameworks) that needs to know a robot safely |
| Swappable? | Yes — pick your models, pick your reactive layer | Yes — works with or without OpenCastor |
| Home | `github.com/craigm26/OpenCastor` | `github.com/RobotRegistryFoundation/robot-md` |

Key sentence: *"OpenCastor is where you tune the brain. ROBOT.md is how the brain knows the body."*

Call out the orthogonality: *"You can use ROBOT.md without OpenCastor — drop it next to any ROS 2 stack, Spot SDK deployment, or custom runtime. You can use OpenCastor without ROBOT.md — the existing `.rcan.yaml` path still works. We're shipping them to compose, not to couple."*

### §5 — Where it sits in the stack (~180 words)

The four-layer mental model, in prose rather than ascii:

> *"Think of it in four layers, each independent: **ROBOT.md** is what the robot is (a declarative file at its root). **OpenCastor** is what the robot runs (a runtime that consumes that file — or any RCAN config — and drives the hardware). **RCAN** is what the robot speaks (the wire protocol for robot-to-robot). **Robot Registry Foundation** is where the robot lives (a neutral registry assigning RRNs). Compose any subset. No layer depends on the others."*

Maybe show the ascii diagram from the repo-design doc. Keep it under 10 lines.

### §6 — Adoption paths for providers (~160 words)

The call to action for agent-harness authors:

> *"If you ship an agent harness, ROBOT.md is a **free upgrade to your robotics story** that doesn't require you to build anything. Point your session-start primitive at the file. That's the whole integration. The spec is [at](https://robotmd.dev/spec/v1), the schema is [machine-validated](https://robotmd.dev/schema/v1/robot.schema.json), and the reference CLI is [Apache 2.0](https://github.com/RobotRegistryFoundation/robot-md). Validator, renderer, and context-emitter are three pip-installable commands. We'd rather you fork the CLI and ship your own than wait for us to."*

Mention the three Claude surfaces explicitly:
- Claude Code — session-start hook (v0.1 today).
- Claude Desktop — MCP server (v0.2, documented pattern).
- Claude Mobile — URL fetch (v0.2, documented pattern).

### §7 — Governance (~140 words)

Short, factual, pre-empts the "who owns this?" question:

- Format spec: CC BY 4.0 (matches RCAN convention).
- CLI + schema: Apache 2.0.
- Stewarded by the **Robot Registry Foundation** at `github.com/RobotRegistryFoundation/robot-md` — vendor-neutral, same home as the registry itself. No single planning-provider owns the standard.
- Contribution bar: small quality PRs welcome; breaking schema changes require a design doc PR first (see `CONTRIBUTING.md`).

Lead sentence: *"No provider should own the file format their competitors' agents have to read."*

### §8 — What's shipping when (~140 words)

- **Today (v0.1)**: spec, schema, 4 worked examples, Python CLI, Claude Code SessionStart hook, documented MCP + URL-bridge patterns, landing at `robotmd.dev`.
- **Next two weeks (v0.2)**: `robot-md register` (RRF integration), working Claude Desktop MCP server, Cloudflare Worker for `robotmd.dev/r/<rrn>` (stable public URL per robot), TypeScript port.
- **Q3 2026 (v1.0)**: spec freeze, conformance test suite, multi-language bindings, potential formal submission to an SDO.

### §9 — Try it in 60 seconds (~120 words)

```bash
# 1. Install the CLI.
pip install robot-md          # once PyPI publish lands — see repo for now

# 2. Write a minimal ROBOT.md for your robot (copy examples/minimal.ROBOT.md).

# 3. Validate it.
robot-md validate ROBOT.md    # green check, exit 0

# 4. Emit the Claude-ready context.
robot-md context ROBOT.md     # prints the block Claude will read

# 5. Wire the SessionStart hook (Claude Code).
mkdir -p ~/.claude/hooks
curl -fsSL https://robotmd.dev/hook | bash
```

Close with: *"Open `claude` in the robot's directory. The planner already knows the robot. That's the whole trick."*

---

## 5. Things the post MUST NOT say

These are tripwires worth naming explicitly:

1. **No mention of the Anthropic outreach strategy, fallback provider list, or adoption proposal specifics.** Those live in `RobotRegistryFoundation/robot-md-private`. The post is public.
2. **No framing of Claude as a preferred provider.** The file is vendor-neutral. Name the other agent harnesses (ChatGPT, Gemini, Ollama, LangChain) in §3 specifically so the post can't be read as a Claude-only pitch.
3. **No claim of production readiness beyond v0.1.** Bob still runs `.rcan.yaml`. MCP server + mobile bridge are documented patterns only. Say so.
4. **No reference to the April 1 post as if it still exists.** That post is being deleted in the same deploy cycle. No internal link to it.
5. **No direct reference to `craigm26/robot-md` as the home.** All repo URLs → `RobotRegistryFoundation/robot-md`. The migration must finish first (see §7 of the strategy doc).

---

## 6. Frontmatter for the post

```yaml
---
title: "ROBOT.md: The Session-Start File for Every Agent That Drives a Robot"
description: "Introducing ROBOT.md — a single-file declaration at your robot's root that any agent harness (Claude Code, ChatGPT, Gemini, Ollama, your own) can read at session start. Vendor-neutral. Apache 2.0. Shipping v0.1 today."
pubDate: 2026-04-17
tags: ["robot-md", "opencastor", "rcan", "robotics", "claude", "agents", "standards"]
author: "Craig Merry"
draft: false
---
```

Slug suggestion: `2026-04-17-robot-md-session-start-file.md`

---

## 7. Companion LinkedIn post

~180 words. Same structure as §1 + §3 + §9 of the blog post. Leads with the session-start insight, shows the file, ends with `pip install`. No mention of outreach, fallbacks, or Anthropic specifics. Saved as `src/content/linkedin/2026-04-17-robot-md.md`.

---

## 8. Images / assets (if any)

- **Hero**: screenshot of `robot-md context bob.ROBOT.md` output with the "# Robot context" block visible. Terminal on dark background.
- **Optional**: the 4-layer ascii diagram from §5 rendered as a small graphic, or left as ascii if the site renders monospace well.
- **Hero image file path**: `public/images/blog/2026-04-17-robot-md.png` (if we generate one).

If generating an image, prompt: *"A minimalist cover illustration in terracotta-and-paper tones — a single stylized document icon labeled 'ROBOT.md' flowing into four abstract icons representing a planner agent, a robot arm, a wire protocol, and a registry. Flat vector, Inter Tight typography. No gradients."*

---

## 9. Verification checklist before publishing

- [ ] Migration executed (Cloudflare green, `craigm26/robot-md` deleted).
- [ ] April 1 blog + LinkedIn deleted in personalsite; commit landed; no internal links surviving.
- [ ] Every repo URL in the post → `RobotRegistryFoundation/robot-md`.
- [ ] No mention of outreach/Anthropic specifics.
- [ ] PyPI install command adjusted to reflect actual publish state on the day of posting (if `pip install robot-md` isn't live, say "install from git" until it is).
- [ ] LinkedIn companion matches tone and avoids the same tripwires.
- [ ] Preview locally via `npm run dev`, check /blog/ listing and the individual post render.
- [ ] Sitemap + RSS regenerate cleanly; no orphan reference to the deleted April 1 post.
- [ ] Social preview (og: image, Twitter card) pulls correctly.

---

## 10. Open questions

1. **Image generation** — do we create a custom hero image today, or ship text-only and revisit? My rec: text-only at launch; add an image in a follow-up commit if the post gets traction.
2. **Cross-posting** — should the LinkedIn companion point back to the blog post, or be self-contained? My rec: self-contained, with a "full writeup" link in the first reply (matches the April 1 LinkedIn pattern you already used).
3. **Slug** — `2026-04-17-robot-md-session-start-file` reads well. Alternatives: `2026-04-17-robot-md-launch`, `2026-04-17-introducing-robot-md`.
4. **Should the post mention the April 1 deletion at all?** My rec: no. Readers who didn't see the old post won't miss it; readers who did will notice the 404 but that's a small cost. Acknowledging the collision publicly is more awkward than clean silence.

---

## 11. Next step after approval

Invoke `superpowers:writing-plans` to produce a step-by-step implementation plan that:

1. Creates the .md file at `personalsite/src/content/blog/2026-04-17-robot-md-session-start-file.md`.
2. Creates the LinkedIn companion at `personalsite/src/content/linkedin/2026-04-17-robot-md.md`.
3. Runs `npm run dev` locally, verifies rendering.
4. Commits both files in one commit ("content: ROBOT.md launch post + LinkedIn").
5. **Does not push** — user drives the deploy once the migration is finalized.
