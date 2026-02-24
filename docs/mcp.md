# OpenCastor MCP Server

OpenCastor now includes an MCP-compatible tool server that exposes high-value runtime operations by reusing existing gateway/API and CLI handlers.

## Start the server

```bash
castor mcp --host 127.0.0.1 --port 8765
```

The server listens over HTTP and accepts JSON-RPC 2.0 requests at:

- `POST /mcp` (MCP methods)
- `GET /health` (server liveness)

## Transport and auth expectations

- **Transport**: HTTP JSON-RPC (`application/json`) over a local or private network endpoint.
- **Auth**: Optional bearer token. If `OPENCASTOR_MCP_TOKEN` is set, clients must send:
  - `Authorization: Bearer <token>`
- If `OPENCASTOR_MCP_TOKEN` is unset, the MCP endpoint is open.

> Recommended production posture: bind to localhost or an internal network and set `OPENCASTOR_MCP_TOKEN`.

## Supported MCP methods

### `initialize`
Returns server metadata and protocol version.

### `tools/list`
Returns tool descriptors and JSON schemas.

### `tools/call`
Invokes a tool with:

```json
{
  "name": "<tool_name>",
  "arguments": { ... }
}
```

## Tool catalog

### 1) `status_health`
Read runtime health and status.

Input schema:
```json
{ "type": "object", "properties": {} }
```

### 2) `command_dispatch`
Dispatch natural-language commands through the active brain.

Input schema:
```json
{
  "type": "object",
  "required": ["instruction"],
  "properties": {
    "instruction": { "type": "string" },
    "image_base64": { "type": "string" }
  }
}
```

### 3) `stop_estop`
Stop immediately or clear an e-stop latch.

Input schema:
```json
{
  "type": "object",
  "properties": {
    "mode": { "type": "string", "enum": ["stop", "clear"], "default": "stop" }
  }
}
```

### 4) `recent_episodes_telemetry`
Read recent memory episodes and `/proc` telemetry snapshot.

Input schema:
```json
{
  "type": "object",
  "properties": {
    "limit": { "type": "integer", "default": 20 },
    "source": { "type": "string" }
  }
}
```

### 5) `config_validate_lint`
Run config lint and RCAN validation using existing CLI handlers.

Input schema:
```json
{
  "type": "object",
  "properties": {
    "config": { "type": "string", "default": "robot.rcan.yaml" }
  }
}
```

## Example requests

### Initialize

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

### List tools

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

### Call `status_health`

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"status_health","arguments":{}}}'
```

### Call `command_dispatch`

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"command_dispatch","arguments":{"instruction":"move forward slowly"}}}'
```
