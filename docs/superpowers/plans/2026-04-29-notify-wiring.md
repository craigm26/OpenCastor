# Notify Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire HiTL gate notifications and AUTHORITY_ACCESS owner notifications through `state.channels` so production deployments actually deliver the messages they advertise. Both gaps share one shape (`notify_fn=None` hook never bound at startup) and ship in one PR.

**Architecture:** A new `NotifyDispatcher` resolver module fans out to channel adapters via `state.channels` + a per-channel `chat_ids` config. `HiTLGateManager` gains a `notify_fn` ctor param; `AuthorityRequestHandler` already has `notify_fn` (we wire it). A new `_wire_notify_dispatch()` helper in `castor/api.py` runs after `_start_channels()` and binds both classes to the dispatcher. New `operator:` YAML block carries `chat_ids` and `owner_channel`.

**Tech Stack:** Python 3.10+, asyncio, FastAPI, pytest, pytest-asyncio (`asyncio_mode=auto`), starlette TestClient. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-29-notify-wiring-design.md`
**Branch:** `feat/notify-wiring` (already created from `origin/main` @ `466a010`)

---

## File Structure

| File | Role | LOC |
|---|---|---|
| `castor/notify_dispatch.py` (new) | `NotifyDispatcher` class. One responsibility: name + chat_id → `channel.send_message_with_retry`. No registry, no queue. | ~70 |
| `castor/hitl_gate.py` (modify) | Add `notify_fn` ctor kwarg; switch `_notify` body to `await self._notify_fn(...)` when set, log-only when `None`. | +6 / -1 |
| `castor/authority.py` (modify) | One-line change in `send_authority_response()` to prefer `state.authority_handler` when present. No signature changes. | +5 |
| `castor/api.py` (modify) | New `_wire_notify_dispatch()` helper. Replace inline `HiTLGateManager(...)` at line 6263 with a deferred wiring after `_start_channels()`. New `state.notify_dispatcher` and `state.authority_handler` fields. | +60 / -3 |
| `tests/test_notify_dispatch.py` (new) | Six dispatcher unit cases. | ~150 |
| `tests/test_hitl_gate.py` (modify) | Two cases for `notify_fn` set/unset behavior. | +60 |
| `tests/test_authority.py` (new) | Two cases — covers existing `notify_fn=None` branch + happy path. | ~80 |
| `tests/test_notify_dispatch_integration.py` (new) | One test: TestClient + recorder channel + pick_place 202, assert ping recorded. | ~120 |

`castor/main.py` may need a parallel update — handled in Task 10 after the api.py path is proven.

---

## Task 0: Verify branch and baseline

**Files:** none

- [ ] **Step 1: Confirm branch and clean state**

Run:
```bash
cd ~/OpenCastor
git status --short
git rev-parse --abbrev-ref HEAD
git log --oneline -2
```

Expected:
- Branch: `feat/notify-wiring`
- Last commit: `b72e921 docs(notify): design spec for HiTL + AUTHORITY_ACCESS notify wiring`
- No uncommitted changes (the spec was already committed)

- [ ] **Step 2: Run baseline tests for the files we'll touch**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_hitl_gate.py tests/test_consent_gate.py -q
```

Expected: all green. If any fail on `main`, stop and ask — that's a pre-existing issue, not something to fix in this PR.

---

## Task 1: NotifyDispatcher — skeleton + happy path (TDD)

**Files:**
- Create: `castor/notify_dispatch.py`
- Create: `tests/test_notify_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify_dispatch.py`:

```python
"""Tests for castor.notify_dispatch.NotifyDispatcher.

Covers the cross-cutting fan-out used by HiTLGateManager._notify and
AuthorityRequestHandler._notify_owner. The dispatcher must be best-effort —
per-channel exceptions are absorbed and logged, never raised.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from castor.channels.base import BaseChannel


class _FakeChannel(BaseChannel):
    """Recorder + configurable-failure channel double."""

    name = "fake"

    def __init__(self, name: str, raises: Exception | None = None):
        # Bypass BaseChannel.__init__ — we don't need rate-limiting machinery
        self.name = name
        self.sends: list[tuple[str, str]] = []
        self._raises = raises
        self.logger = __import__("logging").getLogger(f"OpenCastor.Channel.{name}")

    async def start(self) -> None:  # pragma: no cover — required abstract
        pass

    async def stop(self) -> None:  # pragma: no cover — required abstract
        pass

    async def send_message(self, chat_id: str, text: str) -> None:
        if self._raises is not None:
            raise self._raises
        self.sends.append((chat_id, text))


@pytest.mark.asyncio
async def test_fan_out_happy_path_two_channels():
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannel("whatsapp")
    tg = _FakeChannel("telegram")
    channels = {"whatsapp": wa, "telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100", "telegram": "12345678"},
    )

    result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello bob")

    assert result == {"whatsapp": True, "telegram": True}
    assert wa.sends == [("+15555550100", "hello bob")]
    assert tg.sends == [("12345678", "hello bob")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py::test_fan_out_happy_path_two_channels -xvs
```

Expected: FAIL with `ModuleNotFoundError: No module named 'castor.notify_dispatch'`.

- [ ] **Step 3: Write the minimal implementation**

Create `castor/notify_dispatch.py`:

```python
"""castor/notify_dispatch — channel-name → chat_id → BaseChannel resolver.

Used by:
  - HiTLGateManager._notify (HiTL gate `notify: [whatsapp]` lists)
  - AuthorityRequestHandler._notify_owner (single `owner_channel`)

Best-effort: per-channel exceptions are absorbed and logged; `fan_out` and
`notify_owner` never raise into the caller's request path.

See docs/superpowers/specs/2026-04-29-notify-wiring-design.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("OpenCastor.NotifyDispatch")


class NotifyDispatcher:
    """Resolves channel names through `chat_ids` and dispatches via the
    runtime channel registry (typically `state.channels`).

    `channels_ref` is a 0-arg callable so the dispatcher always sees the
    current channel dict, even after hot-reload swaps the reference.
    """

    def __init__(
        self,
        channels_ref: Callable[[], dict[str, Any]],
        chat_ids: dict[str, str],
        owner_channel: str | None = None,
    ) -> None:
        self._channels_ref = channels_ref
        self._chat_ids = dict(chat_ids)
        self._owner_channel = owner_channel

    async def fan_out(
        self, channel_names: list[str], message: str
    ) -> dict[str, bool]:
        """Send `message` to each named channel's configured chat_id.

        Returns {channel_name: ok}. Per-channel exceptions are absorbed.
        """
        results: dict[str, bool] = {}
        channels = self._channels_ref()
        for name in channel_names:
            chat_id = self._chat_ids.get(name)
            if chat_id is None:
                logger.warning(
                    "no chat_id configured for channel '%s', skipping", name
                )
                results[name] = False
                continue
            ch = channels.get(name)
            if ch is None:
                logger.warning(
                    "channel '%s' has chat_id but is not active this run, skipping",
                    name,
                )
                results[name] = False
                continue
            try:
                ok = await ch.send_message_with_retry(chat_id, message)
                results[name] = bool(ok)
            except Exception as exc:  # noqa: BLE001 — best-effort by contract
                logger.error(
                    "notify dispatch failed for channel '%s': %s", name, exc
                )
                results[name] = False
        logger.info("notify dispatch result: %s", results)
        return results
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py::test_fan_out_happy_path_two_channels -xvs
```

Expected: PASS. Note `BaseChannel.send_message_with_retry` calls `send_message` internally so the recorder captures the (chat_id, text) tuple.

- [ ] **Step 5: Commit**

```bash
cd ~/OpenCastor
git add castor/notify_dispatch.py tests/test_notify_dispatch.py
git commit -m "feat(notify): add NotifyDispatcher fan_out happy path"
```

---

## Task 2: NotifyDispatcher — partial failure (one channel raises)

**Files:**
- Modify: `tests/test_notify_dispatch.py` (add test)
- Verify: `castor/notify_dispatch.py` (no change expected — try/except already in place)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_fan_out_partial_failure_logs_and_continues(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannel("whatsapp", raises=RuntimeError("boom"))
    tg = _FakeChannel("telegram")
    channels = {"whatsapp": wa, "telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100", "telegram": "12345678"},
    )

    with caplog.at_level(logging.ERROR, logger="OpenCastor.NotifyDispatch"):
        result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello")

    assert result == {"whatsapp": False, "telegram": True}
    assert tg.sends == [("12345678", "hello")]
    # Failure logged
    assert any("whatsapp" in r.message and "boom" in r.message
               for r in caplog.records if r.levelname == "ERROR")
```

- [ ] **Step 2: Run the test**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py::test_fan_out_partial_failure_logs_and_continues -xvs
```

Expected: PASS — Task 1's implementation already includes the try/except + ERROR log + continue. If this fails, `send_message_with_retry` may be swallowing the exception differently; inspect and adjust the test (the contract is "dispatcher reports failure", not the exact exception path).

- [ ] **Step 3: Commit**

```bash
cd ~/OpenCastor
git add tests/test_notify_dispatch.py
git commit -m "test(notify): partial failure absorbs exception and continues"
```

---

## Task 3: NotifyDispatcher — channel in `notify:` but missing from `chat_ids`

**Files:**
- Modify: `tests/test_notify_dispatch.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_fan_out_missing_chat_id_skips_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    tg = _FakeChannel("telegram")
    channels = {"telegram": tg}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"telegram": "12345678"},  # no 'whatsapp' entry
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        result = await dispatcher.fan_out(["whatsapp", "telegram"], "hello")

    assert result == {"whatsapp": False, "telegram": True}
    assert tg.sends == [("12345678", "hello")]
    assert any("no chat_id configured for channel 'whatsapp'" in r.message
               for r in caplog.records)
```

- [ ] **Step 2: Run the test**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py::test_fan_out_missing_chat_id_skips_with_warning -xvs
```

Expected: PASS (already covered by Task 1's implementation).

- [ ] **Step 3: Commit**

```bash
cd ~/OpenCastor
git add tests/test_notify_dispatch.py
git commit -m "test(notify): missing chat_id skips with warning"
```

---

## Task 4: NotifyDispatcher — channel in `chat_ids` but not in `state.channels`

**Files:**
- Modify: `tests/test_notify_dispatch.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notify_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_fan_out_inactive_channel_skips_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    channels: dict = {}  # no channels active this run

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        result = await dispatcher.fan_out(["whatsapp"], "hello")

    assert result == {"whatsapp": False}
    assert any("not active this run" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_channels_ref_is_re_read_every_call():
    """Mutating the channel dict between calls must be visible — the
    dispatcher must not snapshot."""
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannel("whatsapp")
    channels: dict = {}  # initially empty

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
    )

    r1 = await dispatcher.fan_out(["whatsapp"], "first")
    assert r1 == {"whatsapp": False}  # channel not yet active

    channels["whatsapp"] = wa  # now becomes active
    r2 = await dispatcher.fan_out(["whatsapp"], "second")
    assert r2 == {"whatsapp": True}
    assert wa.sends == [("+15555550100", "second")]
```

- [ ] **Step 2: Run the tests**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py -k "inactive_channel or channels_ref_is_re_read" -xvs
```

Expected: both PASS (Task 1's lambda-based design supports this).

- [ ] **Step 3: Commit**

```bash
cd ~/OpenCastor
git add tests/test_notify_dispatch.py
git commit -m "test(notify): inactive channel skip + channels_ref re-read invariant"
```

---

## Task 5: NotifyDispatcher — `notify_owner`

**Files:**
- Modify: `castor/notify_dispatch.py`
- Modify: `tests/test_notify_dispatch.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notify_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_notify_owner_happy_path():
    from castor.notify_dispatch import NotifyDispatcher

    wa = _FakeChannel("whatsapp")
    channels = {"whatsapp": wa}

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: channels,
        chat_ids={"whatsapp": "+15555550100"},
        owner_channel="whatsapp",
    )

    ok = await dispatcher.notify_owner("AUTHORITY ACCESS REQUEST: …")

    assert ok is True
    assert wa.sends == [("+15555550100", "AUTHORITY ACCESS REQUEST: …")]


@pytest.mark.asyncio
async def test_notify_owner_no_owner_channel_returns_false_with_warning(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: {},
        chat_ids={},
        owner_channel=None,
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        ok = await dispatcher.notify_owner("anything")

    assert ok is False
    assert any("owner_channel" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_notify_owner_owner_channel_missing_chat_id_returns_false(caplog):
    import logging

    from castor.notify_dispatch import NotifyDispatcher

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: {},
        chat_ids={},
        owner_channel="whatsapp",  # set but no chat_ids[whatsapp] entry
    )

    with caplog.at_level(logging.WARNING, logger="OpenCastor.NotifyDispatch"):
        ok = await dispatcher.notify_owner("anything")

    assert ok is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py -k notify_owner -xvs
```

Expected: FAIL with `AttributeError: 'NotifyDispatcher' object has no attribute 'notify_owner'`.

- [ ] **Step 3: Implement `notify_owner`**

Append to `castor/notify_dispatch.py` (inside the `NotifyDispatcher` class, after `fan_out`):

```python
    async def notify_owner(self, message: str) -> bool:
        """Send `message` to the configured owner channel.

        Returns True on success, False on any failure (no event-loop case is
        a caller concern, not ours). Never raises.
        """
        if not self._owner_channel:
            logger.warning(
                "notify_owner called but no owner_channel configured; "
                "AUTHORITY_ACCESS notification dropped"
            )
            return False
        result = await self.fan_out([self._owner_channel], message)
        return result.get(self._owner_channel, False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py -xvs
```

Expected: ALL pass (8 tests so far in this file).

- [ ] **Step 5: Commit**

```bash
cd ~/OpenCastor
git add castor/notify_dispatch.py tests/test_notify_dispatch.py
git commit -m "feat(notify): add NotifyDispatcher.notify_owner"
```

---

## Task 6: HiTLGateManager — `notify_fn` ctor param + `_notify` body

**Files:**
- Modify: `castor/hitl_gate.py`
- Modify: `tests/test_hitl_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hitl_gate.py` (add a new test class so it's clearly grouped):

```python
class TestHiTLGateManagerNotifyFn:
    """notify_fn injection — wires HiTL gates to the channel dispatcher."""

    @pytest.mark.asyncio
    async def test_notify_fn_invoked_on_start_pending(self):
        """When notify_fn is set, start_pending must schedule a call to it
        with (channels, msg)."""
        import asyncio

        from castor.hitl_gate import HiTLGate, HiTLGateManager

        recorded: list[tuple[list[str], str]] = []

        async def recorder(channels: list[str], msg: str) -> None:
            recorded.append((list(channels), msg))

        gates = [HiTLGate(action_types=["pick_place"], notify=["whatsapp"])]
        mgr = HiTLGateManager(gates, notify_fn=recorder)

        pending_id = mgr.start_pending({"type": "pick_place"}, thought=None)
        assert pending_id  # gate matched → non-empty

        # start_pending fires _notify via asyncio.create_task; flush it
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(recorded) == 1
        channels, msg = recorded[0]
        assert channels == ["whatsapp"]
        assert "pick_place" in msg
        assert pending_id in msg

    @pytest.mark.asyncio
    async def test_notify_fn_none_falls_back_to_log(self, caplog):
        """When notify_fn is None (today's behavior), _notify just logs."""
        import asyncio
        import logging

        from castor.hitl_gate import HiTLGate, HiTLGateManager

        gates = [HiTLGate(action_types=["pick_place"], notify=["whatsapp"])]
        mgr = HiTLGateManager(gates, notify_fn=None)

        with caplog.at_level(logging.INFO, logger="OpenCastor.HiTLGate"):
            pending_id = mgr.start_pending({"type": "pick_place"}, thought=None)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert pending_id
        # The today-behavior log line must still appear
        assert any("HiTL notify channels=" in r.message for r in caplog.records)
```

The existing `tests/test_hitl_gate.py` may not have `pytest` imported at the top — check, and add `import pytest` if needed (it's standard). It probably also doesn't have a class wrapping; if the file uses bare functions, drop the `class TestHiTLGateManagerNotifyFn:` and indent accordingly.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_hitl_gate.py -k "notify_fn_invoked_on_start_pending or notify_fn_none_falls_back" -xvs
```

Expected: FAIL with `TypeError: HiTLGateManager.__init__() got an unexpected keyword argument 'notify_fn'`.

- [ ] **Step 3: Implement the change**

Modify `castor/hitl_gate.py`. First, the constructor at line ~51:

```python
    def __init__(
        self,
        gates: list[HiTLGate],
        audit: Any = None,
        notify_fn: "Callable[[list[str], str], Awaitable[None]] | None" = None,
    ):
        self._gates = gates
        self._audit = audit
        self._notify_fn = notify_fn
        # Long-poll: pending_id -> asyncio.Future[str] ("approve"|"deny")
        self._pending: dict[str, asyncio.Future] = {}
        # Two-step: pending_ids issued via start_pending(), still unresolved
        self._known_pending: set[str] = set()
        # Two-step: resolved decisions waiting for consume_decision()
        self._resolved: dict[str, str] = {}
```

Add at the top of the file with other imports:
```python
from collections.abc import Awaitable, Callable
```

Then replace the body of `_notify` (currently at lines 141-152) with:

```python
    async def _notify(
        self, channels: list[str], pending_id: str, action: dict, thought: Any
    ) -> None:
        """Emit notification to configured channels (best-effort)."""
        action_type = action.get("type", "unknown")
        timeout_s = 30  # default display; caller has actual timeout
        msg = (
            f"⚠️ Authorization required: {action_type} — "
            f"reply 'approve {pending_id}' or 'deny {pending_id}' within {timeout_s}s"
        )
        if self._notify_fn is not None:
            try:
                await self._notify_fn(channels, msg)
            except Exception as exc:  # noqa: BLE001
                logger.error("HiTL notify_fn raised (absorbed): %s", exc)
        else:
            logger.info("HiTL notify channels=%s: %s", channels, msg)
```

The defensive `try/except` around `notify_fn` is belt-and-suspenders — `NotifyDispatcher.fan_out` already absorbs everything, but this protects against any future `notify_fn` injection that doesn't.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_hitl_gate.py -xvs
```

Expected: ALL pass (existing tests + the two new ones). If any pre-existing test fails, it's because the `Callable` import wasn't there or the constructor signature change broke a positional-arg call site in a test — fix the test (use kwargs).

- [ ] **Step 5: Commit**

```bash
cd ~/OpenCastor
git add castor/hitl_gate.py tests/test_hitl_gate.py
git commit -m "feat(hitl): wire HiTLGateManager._notify to optional notify_fn"
```

---

## Task 7: AuthorityRequestHandler — new test file

**Files:**
- Create: `tests/test_authority.py`

This task adds *test coverage* for `AuthorityRequestHandler`. The class itself is unchanged — but the existing `notify_fn=None` branch has no test today, and the integration test in Task 9 will rely on us being able to construct the handler with a real `notify_fn`. Pin both behaviors now.

- [ ] **Step 1: Check whether test_authority.py exists**

Run:
```bash
cd ~/OpenCastor
ls tests/test_authority*.py 2>/dev/null || echo "creating new file"
```

Expected: `creating new file`. (If the file exists, skip Step 2's new-file boilerplate and just append the two test methods.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_authority.py`:

```python
"""Tests for castor.authority.AuthorityRequestHandler.

Pins the two notify_fn behaviors that the cross-cutting notify-wiring PR
relies on: (1) when wired, the handler emits the AUTHORITY ACCESS summary
to notify_fn before returning the response; (2) when notify_fn is None,
the handler logs a warning but still completes the request (today's
behavior — must not regress).
"""

from __future__ import annotations

import logging

from castor.authority import AuthorityRequestHandler


def _valid_payload() -> dict:
    """Builds a minimal AUTHORITY_ACCESS payload that passes validation."""
    return {
        "authority_id": "eu.aiact.notified-body.001",
        "request_id": "req-test-001",
        "requested_data": ["safety_manifest"],
        "justification": "compliance audit",
        "expires_at": 0,  # 0 means "no expiry" per authority.py:229
    }


class TestNotifyOwner:
    def test_notify_fn_receives_authority_access_summary(self):
        recorded: list[str] = []

        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=lambda msg: recorded.append(msg),
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        result = handler.handle(_valid_payload())

        assert len(recorded) == 1
        summary = recorded[0]
        assert "AUTHORITY ACCESS REQUEST" in summary
        assert "eu.aiact.notified-body.001" in summary
        assert "req-test-001" in summary
        assert "safety_manifest" in summary
        assert "compliance audit" in summary
        # Response was still produced
        assert result["request_id"] == "req-test-001"
        assert result["rrn"] == "RRN-000000000003"

    def test_notify_fn_none_logs_warning_and_completes(self, caplog):
        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=None,
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        with caplog.at_level(logging.WARNING, logger="OpenCastor.Authority"):
            result = handler.handle(_valid_payload())

        # Today's protective branch: warning is emitted
        assert any(
            "No notify_fn configured" in r.message for r in caplog.records
        )
        # ... but the response is still produced
        assert result["request_id"] == "req-test-001"

    def test_notify_fn_exception_does_not_break_response(self, caplog):
        def boom(_msg: str) -> None:
            raise RuntimeError("notify channel exploded")

        handler = AuthorityRequestHandler(
            rrn="RRN-000000000003",
            notify_fn=boom,
            trusted_authority_ids={"eu.aiact.notified-body.001"},
        )

        with caplog.at_level(logging.ERROR, logger="OpenCastor.Authority"):
            result = handler.handle(_valid_payload())

        # Existing try/except at authority.py:287-290 absorbs it
        assert any(
            "Failed to notify owner" in r.message for r in caplog.records
        )
        assert result["request_id"] == "req-test-001"
```

The logger name `OpenCastor.Authority` is a guess — verify against the actual `logger = logging.getLogger(...)` at the top of `castor/authority.py`. If different, update the `caplog.at_level` argument. (Most OpenCastor modules use `OpenCastor.<Module>`.)

- [ ] **Step 3: Run the tests**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_authority.py -xvs
```

Expected: PASS — the existing `castor/authority.py` already implements all three behaviors; this is regression coverage. If a test fails because of a logger-name mismatch, fix the test. If a test fails because of an actual code bug surfaced by the new coverage, that's a pre-existing issue — flag it and ask before changing `authority.py` semantics.

- [ ] **Step 4: Commit**

```bash
cd ~/OpenCastor
git add tests/test_authority.py
git commit -m "test(authority): pin notify_fn behavior across set/unset/exception"
```

---

## Task 8: api.py `_wire_notify_dispatch()` helper

**Files:**
- Modify: `castor/api.py`

This is the wiring step. Today `HiTLGateManager` is constructed at `api.py:6263` *before* channels start; we move that into a helper that runs *after* `_start_channels()` so the dispatcher sees `state.channels`. We also instantiate a long-lived `state.authority_handler`.

- [ ] **Step 1: Read the current call site to confirm line numbers**

Run:
```bash
cd ~/OpenCastor
grep -n "HiTLGateManager(\|state\.hitl_gate_manager" castor/api.py | head -10
grep -n "_start_channels\|on_event\|@app.on_event\|lifespan" castor/api.py | head -20
```

Note the exact line numbers — they may have drifted from line 6263 since the spec was written.

- [ ] **Step 2: Add the helper function**

Add a new function in `castor/api.py` near the existing `_start_channels` definition (~line 5856). Place it after `_start_channels` and before `_stop_channels`:

```python
def _wire_notify_dispatch() -> None:
    """Wire HiTLGateManager and AuthorityRequestHandler through NotifyDispatcher.

    Runs after _start_channels() so that state.channels is populated. Idempotent
    — safe to call multiple times (rebuilds the dispatcher and the dependent
    handlers).

    No-op (with info log) when operator.chat_ids and operator.owner_channel are
    both absent from config — preserves today's log-only behavior for installs
    that haven't opted in yet.
    """
    import asyncio

    from castor.authority import AuthorityRequestHandler
    from castor.configure import parse_consent_gates, parse_hitl_gates
    from castor.hitl_gate import HiTLGateManager
    from castor.notify_dispatch import NotifyDispatcher

    config = state.config or {}
    op_cfg = config.get("operator") or {}
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
            owner_channel,
            owner_channel,
        )

    # Compute hitl gates (mirrors today's startup logic so we can validate
    # gate.notify references against chat_ids)
    try:
        hgates = list(parse_hitl_gates(config) or [])
    except Exception as exc:
        logger.debug("parse_hitl_gates failed in wire: %s", exc)
        hgates = []
    try:
        hgates += list(parse_consent_gates(config) or [])
    except Exception as exc:
        logger.debug("parse_consent_gates failed in wire: %s", exc)

    for gate in hgates:
        for ch in gate.notify or []:
            if ch not in chat_ids:
                logger.warning(
                    "HiTL gate notify=[%s] but no operator.chat_ids[%s] entry; "
                    "this channel will be skipped",
                    ch,
                    ch,
                )

    dispatcher = NotifyDispatcher(
        channels_ref=lambda: state.channels,
        chat_ids=chat_ids,
        owner_channel=owner_channel,
    )
    state.notify_dispatcher = dispatcher

    # Rebuild HiTLGateManager with notify_fn now that dispatcher exists
    if hgates:
        state.hitl_gate_manager = HiTLGateManager(
            hgates,
            notify_fn=dispatcher.fan_out,
        )

    # Long-lived authority handler with sync→async adapter for notify_fn
    def _owner_notify(msg: str) -> None:
        try:
            asyncio.create_task(dispatcher.notify_owner(msg))
        except RuntimeError:
            logger.warning(
                "authority notify_fn called outside event loop; skipped"
            )

    rrn = (config.get("metadata") or {}).get("rrn", "RRN-UNKNOWN")
    state.authority_handler = AuthorityRequestHandler(
        rrn=rrn,
        notify_fn=_owner_notify,
    )

    logger.info(
        "NotifyDispatcher wired (%d chat_ids, owner=%s)",
        len(chat_ids),
        owner_channel or "none",
    )
```

Two notes:
1. Authority's `trusted_authority_ids` and `sbom_url`/`firmware_manifest_url` are deliberately omitted at this point — pull them from config if/when the install populates them; today they're not in the config schema and `AuthorityRequestHandler.__init__` already defaults them. If a later requirement adds them, plumb through `op_cfg` or a sibling config block.
2. We import `parse_consent_gates` because Task #880 (already merged) added consent-derived gates that join the HiTL list.

- [ ] **Step 3: Add `notify_dispatcher` and `authority_handler` fields on AppState**

Find the `state` dataclass / object near `api.py:205`. Add two fields next to `channels`:

```python
notify_dispatcher: object = None  # NotifyDispatcher
authority_handler: object = None  # AuthorityRequestHandler
```

(Use the same style the existing fields use — if it's a dataclass, dataclass fields; if it's `class AppState:` with class-level vars, add class-level vars.)

- [ ] **Step 4: Call the helper after `_start_channels()`**

Find the existing startup call to `_start_channels()` in `api.py` (likely inside an `@app.on_event("startup")` async function or in the `lifespan` context manager). After the `await _start_channels()` line, add:

```python
    _wire_notify_dispatch()
```

Also remove the now-redundant inline `HiTLGateManager(...)` construction at the old line ~6263 — `_wire_notify_dispatch()` now owns this. If the existing inline construction is wrapped in a try/except that handles the no-config case, replace the construction line with `pass` and let `_wire_notify_dispatch` handle it (the helper logs an info line for the no-config case).

If you find that the existing inline construction handles the case where `operator:` is absent (HiTL still works in log-only mode), add a small fallback at the start of `_wire_notify_dispatch` that builds `state.hitl_gate_manager` with `notify_fn=None` even when `operator:` is absent — so existing installs without the new YAML keep working:

```python
    # Always build the HiTL manager so existing installs (no operator block)
    # keep working in log-only mode.
    if hgates and state.hitl_gate_manager is None:
        state.hitl_gate_manager = HiTLGateManager(hgates, notify_fn=None)
```

Move the `hgates` computation above the `if not chat_ids and not owner_channel: return` short-circuit so this fallback runs even in the no-config path. (Restructuring detail: do it in whatever way reads cleanly — the contract is "after `_wire_notify_dispatch` returns, `state.hitl_gate_manager` is set if there are any gates, regardless of whether `operator:` exists".)

- [ ] **Step 5: Run the existing test suite to verify no regressions**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_consent_gate.py tests/test_hitl_gate.py tests/test_authority.py tests/test_notify_dispatch.py -xvs
```

Expected: all PASS. The existing `test_consent_gate.py` constructs `HiTLGateManager` directly in `_wire_consent_gate()` (`tests/test_consent_gate.py:154`) so it doesn't go through our new helper — that's fine, it's still exercising the same class.

- [ ] **Step 6: Commit**

```bash
cd ~/OpenCastor
git add castor/api.py
git commit -m "feat(api): _wire_notify_dispatch — bind HiTL+Authority to NotifyDispatcher"
```

---

## Task 9: Integration test — pick_place ping arrives at recorder channel

**Files:**
- Create: `tests/test_notify_dispatch_integration.py`

This is the load-bearing test — it catches the wiring-at-startup failure mode that produced the original bug.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify_dispatch_integration.py`:

```python
"""Integration test for NotifyDispatcher wiring.

Boots a slim FastAPI test app, injects a recorder channel into
state.channels["whatsapp"], populates state.config with the operator
block, runs _wire_notify_dispatch(), then triggers the consent gate via
POST /api/arm/pick_place. Asserts the 202 response *and* that the
recorder channel received the pending_id ping.

This catches the wiring-at-startup failure mode that produced the
opencastor #867 follow-up bug — pure unit tests can't catch it because
they bypass the binding step.
"""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, patch

import pytest


class _RecorderChannel:
    """Minimal channel double that records send_message calls.

    Doesn't subclass BaseChannel — we only need the surface
    NotifyDispatcher actually calls (send_message_with_retry).
    """

    name = "whatsapp"

    def __init__(self) -> None:
        self.sends: list[tuple[str, str]] = []

    async def send_message_with_retry(
        self, chat_id: str, text: str, **_: object
    ) -> bool:
        self.sends.append((chat_id, text))
        return True

    async def send_message(self, chat_id: str, text: str) -> None:
        self.sends.append((chat_id, text))


def _make_client_and_reset(monkeypatch):
    """Borrowed pattern from tests/test_consent_gate.py:104."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.offline_fallback = None
    api_mod.state.provider_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.hitl_gate_manager = None
    api_mod.state.notify_dispatcher = None
    api_mod.state.authority_handler = None
    api_mod.API_TOKEN = None

    from starlette.testclient import TestClient

    from castor.api import app

    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    import contextlib as _contextlib

    @_contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    return TestClient(app, raise_server_exceptions=False)


def test_pick_place_pending_auth_pings_recorder_channel(monkeypatch):
    """Full wiring: pick_place returns 202 + pending_id, AND the recorder
    channel receives a WhatsApp message containing the pending_id."""
    client = _make_client_and_reset(monkeypatch)

    import castor.api as api_mod

    # 1. Inject the recorder as the active 'whatsapp' channel
    recorder = _RecorderChannel()
    api_mod.state.channels = {"whatsapp": recorder}

    # 2. Populate config with the operator block + consent gate
    api_mod.state.config = {
        "consent": {"required": True, "scope_threshold": "control"},
        "operator": {
            "chat_ids": {"whatsapp": "+15555550100"},
            "owner_channel": "whatsapp",
        },
    }

    # 3. Driver + brain mocks (mirrors test_consent_gate.py)
    api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
    from castor.providers.base import Thought

    mock_brain = MagicMock()
    mock_brain.think.return_value = Thought(raw_text="[]", action=[])
    api_mod.state.brain = mock_brain

    # 4. Fire the wiring helper (this is the system-under-test)
    api_mod._wire_notify_dispatch()

    # Sanity — wiring built the dispatcher and rebuilt the gate manager
    assert api_mod.state.notify_dispatcher is not None
    assert api_mod.state.hitl_gate_manager is not None

    # 5. Trigger the consent gate
    with patch(
        "castor.api._capture_live_frame",
        return_value=b"\xff\xd8" + b"\x00" * 1024,
    ):
        resp = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "PENDING_AUTH"
    pending_id = body["pending_id"]
    assert pending_id

    # 6. _notify is fired via asyncio.create_task; let the loop drain.
    # TestClient is synchronous so we need to spin the loop manually.
    # The notify task was created on the loop the request handler ran on;
    # by the time TestClient returns, that loop has already finished the
    # request — but the create_task callback may still be queued. Give it
    # a few ticks.
    import asyncio

    async def _drain():
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(_drain())

    # 7. The recorder must have received exactly one message
    assert len(recorder.sends) == 1, (
        f"recorder.sends={recorder.sends}; expected 1 ping after pick_place 202"
    )
    chat_id, msg = recorder.sends[0]
    assert chat_id == "+15555550100"
    assert "pick_place" in msg
    assert pending_id in msg


def test_pick_place_without_operator_block_falls_back_to_log_only(monkeypatch, caplog):
    """When operator: block is absent, _wire_notify_dispatch must log an info
    line and the recorder must NOT be pinged. Today's behavior preserved."""
    import logging

    client = _make_client_and_reset(monkeypatch)

    import castor.api as api_mod

    recorder = _RecorderChannel()
    api_mod.state.channels = {"whatsapp": recorder}

    # Note: no 'operator' key
    api_mod.state.config = {
        "consent": {"required": True, "scope_threshold": "control"},
    }

    api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
    from castor.providers.base import Thought

    mock_brain = MagicMock()
    mock_brain.think.return_value = Thought(raw_text="[]", action=[])
    api_mod.state.brain = mock_brain

    with caplog.at_level(logging.INFO):
        api_mod._wire_notify_dispatch()

    assert any(
        "operator.chat_ids not configured" in r.message
        for r in caplog.records
    )

    # Gate manager still built (so pick_place still gets gated)
    assert api_mod.state.hitl_gate_manager is not None

    with patch(
        "castor.api._capture_live_frame",
        return_value=b"\xff\xd8" + b"\x00" * 1024,
    ):
        resp = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )

    assert resp.status_code == 202, resp.text

    import asyncio

    async def _drain():
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(_drain())

    # No operator block → no ping
    assert recorder.sends == []
```

- [ ] **Step 2: Run the integration tests**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch_integration.py -xvs
```

Expected: both PASS. If the first test fails because the `asyncio.get_event_loop().run_until_complete(_drain())` errors with "Event loop is closed" or "no current event loop", replace that block with `time.sleep(0.05)` — TestClient may have its own event-loop semantics that don't expose a way to drain pending tasks deterministically. The contract is "the ping arrives within ~50ms"; how we wait is a test-mechanics detail.

If the second test fails with `state.hitl_gate_manager is None` (the gate manager wasn't built when no `operator:` block exists), revisit Task 8 Step 4 — the fallback that builds `state.hitl_gate_manager` with `notify_fn=None` regardless of `operator:` config wasn't added.

- [ ] **Step 3: Commit**

```bash
cd ~/OpenCastor
git add tests/test_notify_dispatch_integration.py
git commit -m "test(notify): integration test — pick_place 202 pings recorder channel"
```

---

## Task 10: castor/main.py — investigate parallel HiTL init, port or TODO

**Files:**
- Modify: `castor/main.py` (only if its HiTL path is live)

The spec called this out as a known unknown. Resolve it now.

- [ ] **Step 1: Investigate whether `main.py`'s HiTL path is live at runtime**

Run:
```bash
cd ~/OpenCastor
grep -rn "_hitl_gate_manager\|main\._hitl" --include='*.py' | grep -v __pycache__
grep -rn "from castor.main import\|from castor import main" --include='*.py' | grep -v __pycache__
grep -n "^def \|^async def " castor/main.py | head -20
grep -n "_active_channels\|_channel_map" castor/main.py | head -10
```

What you're looking for:
- Is `_hitl_gate_manager` (the local variable at `main.py:1098`) ever read/used downstream in `main.py`?
- Does anyone import from `main.py`'s setup function?
- Is `castor.main` an alternative entry point still active (e.g., a CLI command other than `castor gateway`)?

- [ ] **Step 2: Decide based on findings**

**If the code is live** (downstream reads `_hitl_gate_manager` or `_channel_map`):
Port the same wiring inline at `main.py:1098-…`. Build a local `NotifyDispatcher` against `_channel_map` (which is the local equivalent of `state.channels` here), pass `notify_fn=dispatcher.fan_out` to `HiTLGateManager`. Keep it minimal — same lambda+config-read pattern as `_wire_notify_dispatch`. Do NOT extract a shared helper unless there are 3+ call sites; two call sites with slightly different state shapes don't justify the abstraction.

**If the code is dead** (variable never read, function never called):
Leave alone. Add a one-line comment at `main.py:1098`:
```python
        # NOTE: the api.py gateway is the live path for HiTL wiring; see
        # castor/api.py::_wire_notify_dispatch. This block is preserved
        # for legacy main.py invocation but does not wire NotifyDispatcher.
```

**If unclear** (the code path may or may not be reachable):
Default to the dead-code interpretation (add the comment). It's safer — leaving a half-wired path in `main.py` is what created this bug class to begin with.

- [ ] **Step 3: Run the full suite to confirm no break**

Run:
```bash
cd ~/OpenCastor
pytest tests/ -q --ignore=tests/test_long_running 2>&1 | tail -30
```

Expected: green, or at most pre-existing flakes that aren't related to this PR. If any new failure appears, it's likely a `main.py` path you broke — revert the `main.py` change.

- [ ] **Step 4: Commit**

```bash
cd ~/OpenCastor
git add castor/main.py
git commit -m "$(printf 'chore(main): document NotifyDispatcher wiring lives in api.py\n\nOR\n\nfeat(main): port NotifyDispatcher wiring to main.py CLI gateway path')"
# pick whichever subject matches the decision in Step 2
```

If `git diff --cached` shows no changes, skip the commit (unchanged main.py = correct outcome).

---

## Task 11: Lint + full test sweep + open PR

**Files:** none — verification only

- [ ] **Step 1: Lint**

Run:
```bash
cd ~/OpenCastor
ruff format castor/notify_dispatch.py castor/hitl_gate.py castor/api.py tests/test_notify_dispatch.py tests/test_authority.py tests/test_notify_dispatch_integration.py tests/test_hitl_gate.py
ruff check castor/notify_dispatch.py castor/hitl_gate.py castor/api.py tests/test_notify_dispatch.py tests/test_authority.py tests/test_notify_dispatch_integration.py tests/test_hitl_gate.py
```

Expected: no errors. Fix any warnings ruff surfaces (unused imports, line length).

- [ ] **Step 2: Full pytest sweep on touched modules**

Run:
```bash
cd ~/OpenCastor
pytest tests/test_notify_dispatch.py tests/test_authority.py tests/test_hitl_gate.py tests/test_consent_gate.py tests/test_notify_dispatch_integration.py -v
```

Expected: all pass.

- [ ] **Step 3: Wider regression — anything that touches `state.channels`, HiTL, or authority**

Run:
```bash
cd ~/OpenCastor
pytest tests/ -k "channel or hitl or authority or consent or pick_place or notify" -q 2>&1 | tail -40
```

Expected: green.

- [ ] **Step 4: Push branch and open the PR**

Run:
```bash
cd ~/OpenCastor
git push -u origin feat/notify-wiring
gh pr create --title "feat(notify): wire HiTL + AUTHORITY_ACCESS to channels" --body "$(cat <<'EOF'
## Summary

Closes the #867 follow-up — both `HiTLGate._notify` and `authority._notify_owner` advertised channel notifications but never delivered them in production. Single small PR with shared `NotifyDispatcher` resolver.

- New `castor/notify_dispatch.py` — fans out via `state.channels[name].send_message_with_retry`.
- `HiTLGateManager` gains a `notify_fn=None` ctor kwarg (today's log-only behavior preserved when unset).
- `AuthorityRequestHandler` already had `notify_fn=None`; finally bound at startup.
- New `_wire_notify_dispatch()` in `api.py` runs after `_start_channels()` and binds both.
- New `operator:` YAML block: `chat_ids: {channel: chat_id}` + `owner_channel: <name>`.

## Out of scope (separate PRs)

- Inbound `approve <id>` parsing on the channel side.
- Persistent notification queue / outbox.
- Per-fleet routing, escalation policies, owner-registry.

## Test plan

- [x] Unit tests for `NotifyDispatcher` (8 cases: happy / partial fail / missing chat_id / inactive channel / channels_ref re-read / notify_owner happy / notify_owner missing).
- [x] `test_hitl_gate.py` — `notify_fn` set/unset cases.
- [x] New `tests/test_authority.py` — pins `notify_fn` set / unset / exception behavior.
- [x] New `tests/test_notify_dispatch_integration.py` — TestClient + recorder channel proves the wiring-at-startup link works end-to-end.
- [ ] **Manual on bob** (exit criteria — not blocking CI):
  - [ ] Add `operator.chat_ids.whatsapp: "+<num>"` + `owner_channel: whatsapp` to `bob.rcan.yaml`, restart gateway, confirm boot log: `NotifyDispatcher wired (1 chat_ids, owner=whatsapp)`.
  - [ ] `curl -XPOST robot.local:8001/api/arm/pick_place …` triggering the consent gate; observe 202 + pending_id; observe WhatsApp ping arrives within ~5s containing the pending_id.
  - [ ] `POST /api/hitl/authorize` with the pending_id; client retry of pick_place with `consent_pending_id` succeeds; arm moves.
  - [ ] Synthetic AUTHORITY_ACCESS via `POST /rcan` (msg_type 41); observe WhatsApp ping with the AUTHORITY ACCESS REQUEST summary.
  - [ ] Disconnect WhatsApp container/network → re-run pick_place → 202 still returns immediately, `send_message_with_retry` ERROR in logs, no 5xx.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 5: Verify CI on the PR**

Run:
```bash
cd ~/OpenCastor
gh pr checks --watch
```

Expected: all checks green. If a check fails, fix and push — do not merge with red CI (per `feedback_robotmd_release_workflow_no_ci.md` lesson).

---

## Self-review notes (post-write)

**Spec coverage check:**
- "NotifyDispatcher resolver" — Tasks 1-5 ✓
- "HiTLGateManager `notify_fn` kwarg" — Task 6 ✓
- "AuthorityRequestHandler test coverage" — Task 7 ✓
- "`_wire_notify_dispatch` startup helper" — Task 8 ✓
- "Integration test (wiring-at-startup)" — Task 9 ✓
- "main.py parallel init resolution" — Task 10 ✓
- "operator.chat_ids / owner_channel YAML" — covered in Task 8 (config read) and the manual checklist (Task 11)
- "Four invariants (never raise, loud logs, startup validation, preserve None branch)" — invariant 1 in Tasks 1+5 (try/except + return False); invariant 2 in Task 1 (logger.info on result); invariant 3 in Task 8 (validation warnings); invariant 4 in Tasks 6+7 (explicit `notify_fn=None` cases)

No spec gaps.

**Type / signature consistency:**
- `fan_out(channel_names: list[str], message: str) -> dict[str, bool]` — used identically in Tasks 1, 6 (HiTL `notify_fn` shape), 8 (`notify_fn=dispatcher.fan_out`). Match.
- `notify_owner(message: str) -> bool` — used identically in Task 5 and Task 8 (`dispatcher.notify_owner(msg)`). Match.
- `NotifyDispatcher(channels_ref, chat_ids, owner_channel=None)` — instantiated identically in tests and Task 8. Match.
- `state.notify_dispatcher` and `state.authority_handler` field names — added in Task 8 Step 3, referenced in Task 9 reset block. Match.
