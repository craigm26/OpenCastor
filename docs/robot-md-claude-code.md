# ROBOT.md + Claude Code — one command

The fastest way to use Claude Code with an OpenCastor robot. No harness config, no provider picking, no YAML wizard.

## What this is

Claude Code reads a `ROBOT.md` (RCAN 3.0 frontmatter + markdown prose) through an MCP server — [`robot-md-mcp`](https://github.com/RobotRegistryFoundation/robot-md-mcp). Claude now has your robot's identity, capabilities, and safety gates as MCP resources. From there, Claude's Bash tool dispatches commands through OpenCastor's gateway (port 8001) via RCAN.

## Prerequisites

- OpenCastor gateway running (`castor gateway`) — verify with `curl -s http://localhost:8001/health`
- Node 18.20+ (for `npx`)
- Claude Code installed (`claude --version`)
- A `ROBOT.md` for your robot — the [`robot-md`](https://github.com/RobotRegistryFoundation/robot-md) CLI generates a draft:

```bash
pip install robot-md
cd ~/my-robot
robot-md autodetect --write ROBOT.md
# Edit TODOs (robot name, physics.type, dof, capabilities), then:
robot-md validate ROBOT.md
```

## Setup

One line:

```bash
claude mcp add robot-md -- npx -y robot-md-mcp "$(pwd)/ROBOT.md"
```

Verify it's wired:

```bash
claude mcp list | grep robot-md
```

To remove: `claude mcp remove robot-md`.

## What Claude Code sees

Four MCP resources (read at session start):

| URI | MIME | Contents |
|---|---|---|
| `robot-md://<name>/frontmatter` | `application/json` | Full YAML frontmatter |
| `robot-md://<name>/capabilities` | `application/json` | `capabilities[]` array |
| `robot-md://<name>/safety` | `application/json` | E-stop, HITL gates, bounds |
| `robot-md://<name>/body` | `text/markdown` | Prose body (Identity / Capabilities / Safety sections) |

Two tools:

- `validate` — re-validates the served manifest against the v1 schema.
- `render` — strips prose, returns canonical YAML.

Dispatch tools (`invoke_skill`, `query_status`) arrive with `robot-md-mcp` v0.2, gated on the RCAN v0.2 signing decisions.

## Dispatching commands tonight (Path A, v0.1)

While `invoke_skill` is deferred to v0.2, Claude Code can dispatch via its Bash tool directly against the OpenCastor gateway. Example:

```bash
curl -sS -X POST http://localhost:8001/api/arm/pick_place \
  -H 'Content-Type: application/json' \
  -d '{"target":"red_cube","destination":"bowl"}'
```

Claude reads the capability list from `robot-md://<name>/capabilities`, respects HITL gates declared in `robot-md://<name>/safety`, and issues dispatches through RCAN-aware endpoints. The gateway enforces P66 bounds, LoA, and `AUTHORIZE` flows server-side regardless of what the planner requests.

## Example: Bob

Bob (Pi 5 + Hailo-8 + SO-ARM101 6-DOF) ships a canonical `ROBOT.md` at [`robot-md/examples/bob.ROBOT.md`](https://github.com/RobotRegistryFoundation/robot-md/blob/main/examples/bob.ROBOT.md).

```bash
claude mcp add robot-md-bob -- npx -y robot-md-mcp \
  /path/to/robot-md/examples/bob.ROBOT.md
```

Claude Code now knows Bob's 5 capabilities, 0.5 kg payload limit, 100 ms software E-stop, and the HITL gates on destructive actions.

## Troubleshooting

- **`npx` pulls 0.1.3 every time** — that's fine for the quickstart; pin with `robot-md-mcp@0.1.3` if you want reproducibility.
- **MCP server starts but resources show as empty** — check `robot-md validate ROBOT.md`. Missing `metadata.robot_name` is the most common failure.
- **Arm motion dispatches but gateway rejects with `LOA_TOO_LOW`** — OpenCastor's gateway requires Level-of-Assurance 1+ for arm control. Set `OPENCASTOR_LOA=1` or higher in the env before starting the gateway.
- **Multiple robots on one machine** — use distinct MCP names: `claude mcp add robot-md-bob ...` and `claude mcp add robot-md-alice ...`.

## Other agent harnesses — same ROBOT.md, same MCP server

ROBOT.md is planner-agnostic. The `robot-md-mcp` server speaks the open [Model Context Protocol](https://modelcontextprotocol.io). Any harness that speaks MCP can ingest the same file — the command to register is always `npx -y robot-md-mcp /path/to/ROBOT.md`.

| Harness | How to register |
|---|---|
| Claude Code (CLI) | `claude mcp add robot-md -- npx -y robot-md-mcp /path/to/ROBOT.md` |
| Claude Desktop | Add to `claude_desktop_config.json` under `mcpServers` |
| OpenAI Codex CLI / ChatGPT Desktop | Add to the tool's MCP-server config |
| Google Gemini CLI | Add to `~/.gemini/settings.json` under `mcpServers` |
| Cursor / Zed / Cline / Continue.dev | Add via the tool's MCP settings |
| Anything else speaking MCP stdio | Register the `npx` command; that's it |

Write one ROBOT.md. Any provider's agent reads it. That's the point.

## See also

- [`robot-md-mcp`](https://github.com/RobotRegistryFoundation/robot-md-mcp) — the MCP server
- [`robot-md`](https://github.com/RobotRegistryFoundation/robot-md) — the spec and CLI
- [`docs/rcan-integration.md`](./rcan-integration.md) — how OpenCastor wires RCAN
- [`docs/mcp.md`](./mcp.md) — OpenCastor's own MCP server (distinct from robot-md-mcp)
