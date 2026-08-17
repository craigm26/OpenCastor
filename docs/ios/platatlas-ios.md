# The iOS contract: OpenCastor (free) and PlatAtlas iOS (premium)

Two iOS apps consume this repo's surfaces:

- **OpenCastor iOS** (free): single-robot pair / drive / e-stop / offline receipt
  verification.
- **PlatAtlas iOS** (premium, separate product): everything above plus org
  sign-in, multi-robot, eval runs, team chat threads, and evidence upload —
  scaffolded at `rail/apps/platatlas-ios` with its spec at
  `rail/docs/specs/2026-07-22-platatlas-ios-mvp.md`.

This document **freezes** what those apps depend on, so changes here are made
knowingly. Nothing in this file adds new behavior; it records what
`opencastor==3.*` (3.0.2 at time of writing — keep the pin, see
[pypi-versioning](../pypi-versioning.md)) already does.

## 1. The pairing QR payload (v1) — frozen

Built by `castor.pairing.build_pair_payload` (`castor/pairing.py`;
`PAIR_PAYLOAD_VERSION = 1`, "The iOS app (T-012) parses on this"):

| Field | Required | Meaning |
|---|---|---|
| `v` | yes | Schema version. Clients MUST reject any value other than `1`. |
| `gateway_url` | yes | The **robot-md-gateway** base URL (default `:8080`) — signed-receipt `/v1/invoke` lives here. |
| `bearer` | yes | Token for **robot-md-gateway** (from `bearers.yaml`, preferring tier `actuate`). See §2 — it does NOT authenticate the castor API. |
| `manifest_path` | yes | Absolute, gateway-host-local ROBOT.md path. Must ride in the QR: every `InvokeEnvelope` requires it and the phone cannot guess it. |
| `rrn` | yes | The robot's registry number. |
| `estop_url` | no | E-stop endpoint (the castor gateway's `/api/stop`). **Omitted entirely when absent — never `null`.** |

Change policy: any breaking change bumps `PAIR_PAYLOAD_VERSION`; additive
optional fields are allowed within `v: 1`.

### How the payload reaches the app — frozen

The QR encodes a **universal link**, not the JSON:

```
https://opencastor.com/pair#v1.<unpadded base64url of the compact payload JSON>
```

- `v1.` is the **envelope** version (`castor.pairing.PAIR_LINK_SCHEMA`), separate
  from the payload's own `v`: it versions the encoding, so a client knows what it
  was handed before it parses. Clients MUST reject an unrecognised tag.
- The base64url body is unpadded; add `=` back to a multiple of 4 before
  decoding. Reference implementation:
  `castor.pairing.encode_pair_fragment` / `decode_pair_fragment`.
- The payload is **only ever** in the fragment. It carries a live actuate bearer,
  and fragments are not sent to servers. Nothing may put a payload field in the
  path or the query.
- The origin in front of the `#` is not load-bearing for pairing — it only
  decides which explainer page a phone WITHOUT the app lands on. A self-hosted
  `/pair` still pairs.
- `castor pair --no-link` / `castor up --no-link` fall back to a QR containing
  the raw compact JSON, which is what shipped before universal links. Clients
  should keep accepting both.

## 2. Two auth domains — do not conflate them

- The QR `bearer` authenticates **robot-md-gateway** (`gateway_url`) only.
- The **castor gateway API** (`castor/api.py`, default `:8001`) has its own
  layered auth (`verify_token`): multi-user JWT → RCAN JWT → static
  `OPENCASTOR_API_TOKEN` → open access when nothing is configured. Header
  `Authorization: Bearer <token>` or `?token=` (WebSockets use `?token=`).
- Clients derive the castor API base from `estop_url` (strip the trailing
  `/api/stop`) when present; otherwise the operator enters it in the app.

## 3. The endpoint set the iPad consumes — frozen shapes

All on the castor gateway (`castor/api.py`), auth per §2:

| Endpoint | Role | Shape notes |
|---|---|---|
| `POST /api/test/run` | operator | Body `{"suite": "full"\|"embedding"\|"fast"}` → `{"status": "started", "suite": …}`; `409` while a run is in progress. **Runs a pytest subprocess.** |
| `GET /api/test/status` | operator | `{running, started_at, result}`; `result = {returncode, stdout, stderr, passed, suite, completed_at}` — a pytest suite result, not a skill scorecard. |
| `GET /api/harness` | operator | Current harness config (skills / hooks / context / model tiers / trajectory / max_iterations). |
| `POST /api/harness` | **admin** | Same fields; `hooks.p66_audit: false` is rejected (`422`); safety/auth/p66 top-level keys are immune. |
| `POST /api/harness/apply-champion` | operator | Optional `{"dry_run": true}` → `{applied, candidate_id, score, config}` or `{applied: false, reason: "no_champion_available"\|"config_not_loaded"\|…}`. |
| `GET /api/research/status` | operator | `{champion, last_run, queue_depth, next_run_estimate, total_runs, search_space_size}`. |
| `GET /api/benchmark/results?limit=50` | operator | `{results: […], count}`; rows newest-first, each `{timestamp, results: [{provider, model, p50_ms, p95_ms, mean_ms, tokens_per_s, status, rounds, …}]}`. |
| `GET /api/safety/manifest` | operator | P66 manifest. |
| `POST /api/stop` | operator | `{"status": "stopped"}` — the e-stop target. |
| `POST /api/hitl/authorize` | admin | `{"pending_id": …, "decision": "approve"\|"deny"}` → `{ok, pending_id, decision}`; `404` on unknown id. |
| `WS /ws/telemetry` | `?token=` | ~200 ms frames `{ts, robot, loop_count, avg_latency_ms, camera, driver, depth, provider, using_fallback}`; client may send `{"cmd": "stop"}`; close `1008` on auth failure. |
| `WS /ws/safety` | `?token=` | ~2 Hz `{events: […]}`. |

## 4. Known gaps the apps design around (deferred backend candidates)

- **No remote benchmark start.** `castor benchmark` is CLI-only. Candidate:
  `POST /api/benchmark/run`.
- **No HTTP skill-scorecard surface.** The rich eval scorecard
  (`castor/eval_harness.py` `EvalResult`: `skill_name`, `pass_count`,
  `total_count`, `pass_rate`, `model_used`,
  `cases[{id, passed, triggered, should_trigger, checks, latency_ms, error}]`)
  is only reachable via the `castor eval` CLI. Apps may render imported CLI
  output against this exact snake_case shape. Candidate: a read-only
  `GET /api/eval/…` surface.

Neither candidate is scheduled by this document; it only names them so the gap
is tracked where the contract lives.
