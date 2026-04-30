# Notify Wiring — Cross-Cutting HiTL + Authority Channel Dispatch

**Status:** Design (brainstormed 2026-04-29)
**Owner:** craigm26
**Closes:** #867 follow-up — "HiTLGate.notify is a no-op in production" + same gap in `authority.py:_notify_owner`
**PR target:** small, single PR, `feat(notify): wire HiTL + AUTHORITY_ACCESS to channels`

## Problem

Two production code paths advertise notifications but never deliver them:

1. **`castor/hitl_gate.py:_notify`** (`hitl_gate.py:141`). Gates declare `notify: [whatsapp]` in YAML; `start_pending` and `check` both call `await self._notify(...)`; the body just logs `"HiTL notify channels=%s: %s"` and returns. Comment explicitly says `"Actual channel dispatch is application-layer; this logs the intent."` — the application layer was never wired.

2. **`castor/authority.py:_notify_owner`** (`authority.py:285`). `AuthorityRequestHandler` takes `notify_fn: Optional[Callable[[str], None]] = None`; if it is `None`, `_notify_owner` logs the warning `"No notify_fn configured — owner not notified of AUTHORITY_ACCESS"`. The runtime never instantiates `AuthorityRequestHandler` with a real `notify_fn` (only `tests/` and the per-call `send_authority_response` builder do).

Result: an operator approving a pick-place action via the §867 two-step flow has to grep gateway logs for the pending_id; an `AUTHORITY_ACCESS (41)` request from a regulator silently never pings the owner.

The two gaps share one shape (`notify_fn=None` hook on a long-lived object, never bound at startup), so a single small PR can close both rather than two near-identical follow-ups.

## Non-goals

- Inbound channel-side approval (operator typing `approve <id>` into WhatsApp). Memory `project_opencastor_867_shipped_2026_04_29.md` records this as "deserves its own PR" — the current `POST /api/hitl/authorize` HTTP path already accepts the pending_id from the 202 body, so notify-only fully unblocks.
- Persistent notification queue / outbox / retry beyond what `BaseChannel.send_message_with_retry` already provides.
- Per-fleet routing, escalation policies, multi-tenant owner registry. Single-robot install (bob) is the only deployed shape today.
- New channel adapters. Reuses `castor/channels/*` exactly as-is.

## Architecture

One new module, two minimal touch-ups, one startup glue point.

```
                       state.channels: dict[str, BaseChannel]
                                  ▲
                                  │  reads
                                  │
HiTLGateManager._notify ──► NotifyDispatcher.fan_out(channels, message)
                                  ▲
AuthorityRequestHandler          │
   ._notify_owner    ─────► (closure capturing dispatcher + owner_channel)
```

- `castor/notify_dispatch.py` (new): `NotifyDispatcher` resolves channel names → `chat_ids` → `state.channels[name].send_message_with_retry(...)`. Best-effort, never raises into callers.
- `castor/hitl_gate.py`: `HiTLGateManager.__init__` gains `notify_fn: Callable[[list[str], str], Awaitable[None]] | None = None`. `_notify` `await`s it when set; falls back to today's log-only when `None`.
- `castor/authority.py`: no signature change. `_notify_owner` already calls `self.notify_fn(message)` when set. Startup wiring binds `notify_fn` to a sync→async adapter that schedules `dispatcher.notify_owner(message)`.
- `castor/api.py`: a new `_wire_notify_dispatch()` helper, called after `_start_channels()`, builds the dispatcher, rebuilds `HiTLGateManager` with `notify_fn`, and instantiates a long-lived `state.authority_handler`. (`castor/main.py` has a parallel `HiTLGateManager` init that may or may not be live — see Files Touched.)

### Why this shape

- **No new channel registry.** `state.channels` is already that. The dispatcher is a thin resolver, not a manager.
- **No queue / outbox.** Out of scope per "small PR" framing. `send_message_with_retry`'s 3-attempt exponential backoff is the existing transient-failure absorber.
- **Callable injection over module-level singleton.** Matches the existing `notify_fn=None` style in `authority.py` and `services/rrf_poller.py`. Keeps both classes testable without monkey-patching.
- **`channels_ref` is a 0-arg lambda over `state.channels`**, not a snapshot. Survives any future hot-reload that swaps the dict.

## Components

### `castor/notify_dispatch.py` (new, ~60 LOC incl. docstrings)

```python
class NotifyDispatcher:
    def __init__(
        self,
        channels_ref: Callable[[], dict[str, BaseChannel]],
        chat_ids: dict[str, str],
        owner_channel: str | None = None,
    ) -> None: ...

    async def fan_out(self, channel_names: list[str], message: str) -> dict[str, bool]:
        """Send `message` to each named channel's configured chat_id.

        Returns {channel_name: ok}. Per-channel exceptions are caught and
        logged. Skips (with WARNING) any name not in chat_ids or not in
        channels_ref(). Uses BaseChannel.send_message_with_retry.
        """

    async def notify_owner(self, message: str) -> bool:
        """Send `message` to owner_channel only. Returns ok. Logs+absorbs."""
```

### `castor/hitl_gate.py` — diff surface

```python
class HiTLGateManager:
    def __init__(
        self,
        gates: list[HiTLGate],
        audit: Any = None,
        notify_fn: Callable[[list[str], str], Awaitable[None]] | None = None,
    ):
        self._gates = gates
        self._audit = audit
        self._notify_fn = notify_fn
        # ... rest unchanged
```

`_notify` body, after building `msg`:

```python
if self._notify_fn is not None:
    await self._notify_fn(channels, msg)
else:
    logger.info("HiTL notify channels=%s: %s", channels, msg)  # today
```

Both call sites (`start_pending` line 109, `check` line 182) keep their existing `_notify(...)` invocation. The outer `asyncio.create_task` at `start_pending:108` stays — `start_pending` is sync and must remain non-blocking.

### `castor/authority.py` — no signature change

`AuthorityRequestHandler.__init__` and `_notify_owner` are unchanged. We do introduce one runtime change:

- A long-lived `state.authority_handler` instance, built once at startup with `notify_fn` wired.
- `send_authority_response()` prefers `state.authority_handler` when present, falls back to per-call construction (preserves CLI/test callers).

### `castor/api.py` startup glue (new helper, called after `_start_channels()`)

```python
def _wire_notify_dispatch() -> None:
    op_cfg = (state.config or {}).get("operator") or {}
    chat_ids = op_cfg.get("chat_ids") or {}
    owner_channel = op_cfg.get("owner_channel")

    if not chat_ids and not owner_channel:
        logger.info(
            "operator.chat_ids not configured — HiTL/AUTHORITY notifications "
            "log only (current behavior)"
        )
        return

    # Validation warnings (non-fatal)
    for ch_name in chat_ids:
        if ch_name not in state.channels:
            logger.warning(
                "operator.chat_ids[%s] configured but channel not active this run",
                ch_name,
            )
    if owner_channel and owner_channel not in chat_ids:
        logger.warning(
            "operator.owner_channel=%s but no chat_ids[%s] entry; "
            "AUTHORITY_ACCESS notifications will fail",
            owner_channel, owner_channel,
        )
    for gate in (_hgates or []):
        for ch in gate.notify:
            if ch not in chat_ids:
                logger.warning(
                    "HiTL gate notify=[%s] but no operator.chat_ids[%s] entry; "
                    "this channel will be skipped",
                    ch, ch,
                )

    state.notify_dispatcher = NotifyDispatcher(
        channels_ref=lambda: state.channels,
        chat_ids=chat_ids,
        owner_channel=owner_channel,
    )

    # Rebuild HiTLGateManager with notify_fn now that dispatcher exists
    state.hitl_gate_manager = HiTLGateManager(
        _hgates,
        audit=audit,
        notify_fn=state.notify_dispatcher.fan_out,
    )

    # Long-lived authority handler with sync→async adapter for notify_fn
    def _owner_notify(msg: str) -> None:
        try:
            asyncio.create_task(state.notify_dispatcher.notify_owner(msg))
        except RuntimeError:
            logger.warning("authority notify_fn called outside event loop; skipped")

    state.authority_handler = AuthorityRequestHandler(
        rrn=_rrn,
        notify_fn=_owner_notify,
        trusted_authority_ids=_trusted_ids,
        sbom_url=_sbom_url,
        firmware_manifest_url=_firmware_url,
    )

    logger.info(
        "NotifyDispatcher wired (%d chat_ids, owner=%s)",
        len(chat_ids), owner_channel or "none",
    )
```

Order matters: channels start → dispatcher built → HiTL manager rebuilt → authority handler bound. The rebuild of `HiTLGateManager` is the one slightly awkward bit (today's init at `api.py:6263` builds it before channels exist), so the helper runs *after* `_start_channels()` and replaces the instance.

## Config schema

```yaml
# bob.rcan.yaml addition
operator:
  chat_ids:
    whatsapp: "+15555550100"      # E.164 for whatsapp/twilio
    telegram: "12345678"          # numeric telegram chat_id
    discord:  "987654321098765432"
  owner_channel: whatsapp         # which entry AuthorityRequestHandler uses
```

Semantics:
- `operator.chat_ids[<name>]`: destination on channel `<name>`. Used by HiTL fan-out (matched against gate `notify:` lists).
- `operator.owner_channel`: single channel name; AUTHORITY_ACCESS notifications go to `chat_ids[owner_channel]`.
- Both keys optional. Missing both → log-only fallback (current behavior, preserves all existing installs).

`owner_channel` and HiTL `chat_ids` are kept distinct because RCAN §8 (HiTL operator approval) and §41 (AUTHORITY_ACCESS owner notification) are different roles. On bob both happen to be the same WhatsApp number; on a future fleet/enterprise install they may diverge.

## Data flow

### HiTL two-step (the §867 path)

```
POST /api/arm/pick_place
  └─► state.hitl_gate_manager.start_pending(action, thought)
        ├─► creates pending_id
        ├─► logger.info("HiTL pending (two-step): id=… notify=[whatsapp]")
        └─► asyncio.create_task(self._notify([whatsapp], pending_id, action, thought))
              └─► msg = "⚠️ Authorization required: pick_place — reply 'approve <id>' or 'deny <id>' within 30s"
                  └─► await self.notify_fn([whatsapp], msg)
                        └─► NotifyDispatcher.fan_out(["whatsapp"], msg)
                              ├─► chat_id = chat_ids["whatsapp"] = "+15555550100"
                              ├─► ch = channels_ref()["whatsapp"]
                              └─► await ch.send_message_with_retry("+15555550100", msg)

api.py returns 202 + {"pending_id": "<uuid>", "state": "PENDING_AUTH"}
                                                  ⬇
                                       Operator sees the WhatsApp ping
                                                  ⬇
                              POST /api/hitl/authorize {pending_id, decision: "approve"}
                                                  ⬇
                              Client retries pick_place with consent_pending_id=<id>
```

The `create_task` wrap (existing at `hitl_gate.py:108`) keeps WhatsApp send latency off the HTTP request path.

### HiTL long-poll (`HiTLGateManager.check`, line 167-188)

Same change site (`_notify` body). The `await self._notify(...)` at line 182 now actually dispatches when `notify_fn` is set.

### AUTHORITY_ACCESS (RCAN §41)

```
POST /rcan { msg_type: 41, payload: {...} }
  └─► state.authority_handler.handle(payload)
        ├─► builds summary "AUTHORITY ACCESS REQUEST\n  Authority: …"
        ├─► self._notify_owner(summary)
        │     └─► self.notify_fn(summary)              # sync callable
        │           └─► asyncio.create_task(           # bound at startup
        │                  state.notify_dispatcher.notify_owner(summary)
        │              )
        │                 └─► ch = channels_ref()["whatsapp"]
        │                 └─► await ch.send_message_with_retry(chat_ids["whatsapp"], summary)
        ├─► self._log_to_chain(req, "received")
        └─► … validation → response …
```

## Error handling

Four invariants the implementation must preserve.

**I-1: never raise into the caller's request path.**
- `NotifyDispatcher.fan_out` and `notify_owner` wrap each per-channel send in `try/except Exception`, return ok-flags, never raise.
- The Authority `_owner_notify` adapter wraps `asyncio.create_task` in `try/except RuntimeError` for the no-loop case. Anything else is caught by `_notify_owner`'s existing `try/except` at `authority.py:287-290`.

**I-2: failures must be loud in logs even when silent to callers.**
- Existing log `"HiTL pending (two-step): id=… notify=[whatsapp]"` (`hitl_gate.py:99-104`) stays.
- Dispatcher adds `"notify dispatch result: whatsapp=ok telegram=fail(timeout) | id=<pending_id>"` after each `fan_out`. INFO on success, ERROR on any failure.
- Authority adds `"notify_owner result: whatsapp=ok | request_id=<id>"`.
- Ops grep for `id=<uuid>` and see the full lifecycle even when a messenger drops.

**I-3: misconfigured `chat_ids` surfaces at startup, not on first incident.**
- `_wire_notify_dispatch()` warns about: chat_ids entries with no matching channel, owner_channel with no chat_ids entry, HiTL `notify:` channels with no chat_ids entry.
- Non-fatal — channels may be lazily started, fleet configs may include sibling robots' destinations.

**I-4: the `notify_fn=None` branch in both classes stays intact.**
- Existing tests constructing `HiTLGateManager(gates)` and `AuthorityRequestHandler(rrn=...)` without `notify_fn` keep passing without modification.
- Verified by *not* removing the `if self.notify_fn:` branch in either class.

### Edge cases

| Case | Behavior |
|---|---|
| `operator` block missing | `_wire_notify_dispatch` logs info, returns; today's behavior preserved. |
| `operator.chat_ids = {}` (explicit empty) | Dispatcher built with empty map; fan_out is per-channel WARNING + skip; deliberate non-fallback so the explicit YAML isn't silently ignored. |
| `owner_channel` set, `chat_ids[owner_channel]` missing | Startup WARNING; `notify_owner` returns False every call; authority response still succeeds (I-1). |
| Channel listed in HiTL `notify:` but missing from `chat_ids` | Per-call WARNING + skip; sibling channels still attempted. |
| Channel in `chat_ids` but not in `state.channels` (channel not started this run) | Per-call WARNING + skip. |
| `send_message` raises after retries | `send_message_with_retry` returns False; dispatcher logs ERROR; request path unaffected. |
| Authority `_notify_owner` called with no event loop (sync caller) | `RuntimeError` caught by `_owner_notify` adapter; warning logged; no crash. |
| Channel send takes 30s | HTTP 202 returned immediately via `create_task`; operator gets a delayed ping. |
| Same pending_id calls `_notify` twice | Today's code calls it once (start_pending xor check). One ping per pending. No dedup needed. |

## Testing

### Unit (new `tests/test_notify_dispatch.py`)

A `_FakeChannel(BaseChannel)` test double records `send_message` calls and can be configured to raise. Six cases:

1. `fan_out` happy path — both channels succeed; correct `(chat_id, msg)`; returns `{wa: True, tg: True}`.
2. `fan_out` partial failure — one raises after retries; returns `{wa: True, tg: False}`; never raises; ERROR logged.
3. `fan_out` channel in `notify:` but not in `chat_ids` — skipped + WARNING; `state.channels` untouched.
4. `fan_out` channel in `chat_ids` but not in `channels_ref()` — skipped + WARNING.
5. `notify_owner` happy path; `notify_owner` with missing `owner_channel` returns False + WARNING.
6. `channels_ref` re-read every call — mutate dict between two `fan_out` calls; second sees new shape.

### Unit extension to `tests/test_hitl_gate.py`

7. `HiTLGateManager(gates, notify_fn=async_recorder)` + `start_pending` → recorder receives `(["whatsapp"], "⚠️ Authorization required: …")` once. `await asyncio.sleep(0)` flushes the `create_task`.
8. `HiTLGateManager(gates, notify_fn=None)` → `start_pending` doesn't crash; recorder never called; today's log line emitted (caplog).

### Unit (new `tests/test_authority.py`)

9. `AuthorityRequestHandler(rrn=..., notify_fn=lambda m: recorder.append(m))` + `handle(payload)` → recorder receives the exact summary string built at `authority.py:213-221`.
10. `AuthorityRequestHandler(rrn=..., notify_fn=None)` → `handle` succeeds; warning logged. (Protects today's behavior.)

### Integration (new `tests/test_notify_dispatch_integration.py`)

11. Boot a slim FastAPI test app via `TestClient`. Inject `_RecorderChannel` into `state.channels["whatsapp"]`. Set `state.config = {"operator": {"chat_ids": {"whatsapp": "+15555550100"}, "owner_channel": "whatsapp"}}`. Run `_wire_notify_dispatch()`. `POST /api/arm/pick_place` with the consent gate enabled. Assert: 202; body has `pending_id`; `recorder.sends` has exactly one entry with `chat_id="+15555550100"` and `msg` starting `"⚠️ Authorization required: pick_place"`.

This catches the wiring-at-startup failure mode that produced the bug in the first place. Pure unit tests can't catch it because they bypass the binding step.

### Manual checklist (in PR description, exit criteria — not blocking CI)

- [ ] On bob: add `operator.chat_ids.whatsapp: "+<my-num>"` + `owner_channel: whatsapp` to `bob.rcan.yaml`, restart gateway, confirm boot log: `"NotifyDispatcher wired (1 chat_ids, owner=whatsapp)"`.
- [ ] `curl -XPOST robot.local:8001/api/arm/pick_place …` triggering the consent gate; observe 202 + pending_id; observe WhatsApp ping arrives within ~5s containing the pending_id.
- [ ] `POST /api/hitl/authorize` with the pending_id; client retry of `pick_place` with `consent_pending_id` succeeds; arm moves.
- [ ] Synthetic AUTHORITY_ACCESS via `POST /rcan` (msg_type 41); observe WhatsApp ping with the AUTHORITY ACCESS REQUEST summary.
- [ ] Disconnect WhatsApp container/network → re-run pick_place → 202 still returns immediately, `send_message_with_retry` ERROR in logs, no 5xx.

## Files touched (estimate)

| File | Change |
|---|---|
| `castor/notify_dispatch.py` | new, ~60 LOC |
| `castor/hitl_gate.py` | +1 ctor param, ~5-line `_notify` body change |
| `castor/authority.py` | no signature change; 1-line `send_authority_response` fallback |
| `castor/api.py` | new `_wire_notify_dispatch()` helper (~50 LOC), 1 call site after `_start_channels()`, replace inline `HiTLGateManager(...)` at line 6263. This is the primary wiring site — `castor gateway` (per CLAUDE.md) runs api.py. |
| `castor/main.py` | secondary surface — has its own `HiTLGateManager` init at line 1098 against local `_active_channels` / `_channel_map`, **not** `state.channels`. Whether main.py participates in HiTL/Authority notification at runtime needs one grep at implementation time (does anything import `_hitl_gate_manager` from main.py, or is it dead-on-the-vine since the api.py gateway took over?). If live, port the same wiring against the local channel map; if dead, leave alone and add a TODO comment. **Resolution lives in the writing-plans phase**, not here. |
| `bob.rcan.yaml` | new `operator:` block (manual; gitignored) |
| `tests/test_notify_dispatch.py` | new, 6 cases |
| `tests/test_hitl_gate.py` | +2 cases |
| `tests/test_authority.py` | new, 2 cases |
| `tests/test_notify_dispatch_integration.py` | new, 1 case |

## Out of scope (for follow-ups, not this PR)

- Inbound channel-side `approve <id>` / `deny <id>` parsing (separate cross-cutting PR — touches `BaseChannel.handle_message` + every channel adapter).
- Persistent notification queue / outbox.
- Per-fleet routing or escalation.
- Owner-registry integration with RRF.
