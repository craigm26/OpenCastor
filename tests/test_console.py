"""castor.console — the robot's chat brains, tested where the PORT could lose them.

This surface spent a week as bench files in two robots' home directories before
it moved into the package. Every test here pins something that was only ever
protected by the fact that one person knew it: the curated model ladder, the
RAM-fit rule that keeps a Pi from OOM-killing itself mid-drive, the ':latest'
tag that made an installed model look missing, and — the one that costs real
money when it breaks — the metered API key that must never reach the `claude`
child process.

The rest pins the NEW thing: /surface, so a phone's picture of a robot stops
being frozen at QR-scan time.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

#: Not a credential — a scratch string, generated nowhere and stored nowhere.
TOKEN = "oc_console_test_only_not_a_real_token"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch ROBOT_HOME with the console configured to serve from it."""
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.setenv("CONSOLE_TOKEN", TOKEN)
    monkeypatch.delenv("CHAT_UPSTREAM", raising=False)
    # Both now read at call time, so the operator's own shell must not decide
    # what these tests assert about the defaults.
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    return tmp_path


@pytest.fixture
def client(home):
    from castor.console.app import build_app

    return TestClient(build_app())


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def rc_car_manifest(home: Path) -> Path:
    """The manifest `castor up` actually generates, written into the home.

    A real fixture rather than a hand-written one: /surface's whole job is to
    describe the robot this package brings up, and a manifest invented for a
    test cannot go stale in the same direction as the template.
    """
    from castor.up import UpPlan, render

    plan = UpPlan(name="testbot", home=home, archetype="rc-car",
                  rrn="RRN-LOCAL-abc123", robot_uuid="u-1", base_port=8080)
    path = home / "ROBOT.md"
    path.write_text(render("ROBOT.md.tmpl", plan))
    return path


def many_capability_manifest(home: Path, count: int = 14) -> Path:
    """A robot rich enough that its surface CANNOT fit in the QR's byte budget."""
    caps = [f"arm.step{i:02d}" for i in range(count)]
    lines = ["---", "metadata:", "  robot_name: many-caps",
             "  rrn: RRN-000000000099", "capabilities:"]
    lines += [f"  - {c}" for c in caps]
    lines.append("capability_contracts:")
    for cap in caps:
        lines += [f"  {cap}:", "    args:", "      target:",
                  "        kind: string", "        required: true",
                  "      speed:", "        kind: float", "        default: 0.2",
                  "    preconditions:", "      - kind: backend_resolved"]
    lines += ["safety:", "  estop:", "    software: true", "  hitl_gates:",
              "    - scope: destructive", "      require_auth: true",
              "vision:", "  object_descriptors:", "    - id: red_block",
              "    - id: blue_block", "---", "", "# many-caps", ""]
    path = home / "ROBOT.md"
    path.write_text("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# The console bearer — fail closed, every endpoint but liveness
# ---------------------------------------------------------------------------


def test_an_unauthenticated_request_is_refused(client):
    assert client.get("/surface").status_code == 401
    assert client.get("/gaps").status_code == 401
    assert client.get("/models/suggestions").status_code == 401


def test_the_wrong_bearer_is_refused(client):
    for headers in ({"Authorization": "Bearer wrong"},
                    {"Authorization": TOKEN},  # bare token, no scheme
                    # Non-ASCII, sent as raw bytes the way a mangled client
                    # would: the comparison is constant-time over BYTES, so this
                    # is a 401 and never a 500 from compare_digest refusing a
                    # non-ASCII str.
                    {"Authorization": "Bearer wröng".encode()},
                    {}):
        assert client.get("/surface", headers=headers).status_code == 401
    assert client.get("/surface", params={"token": "wröng"}).status_code == 401


def test_the_query_string_form_is_the_same_token(client, home):
    # It exists so a feed URL can carry it in an <img> tag; it must not be a
    # second, weaker credential.
    rc_car_manifest(home)
    assert client.get("/surface", params={"token": TOKEN}).status_code == 200
    assert client.get("/surface", params={"token": "wrong"}).status_code == 401


def test_a_console_with_no_token_configured_serves_nobody(tmp_path, monkeypatch):
    # Fail CLOSED. An unset CONSOLE_TOKEN is a missing EnvironmentFile, not
    # permission to hand the robot's brains to the LAN.
    from castor.console.app import build_app

    monkeypatch.setenv("ROBOT_HOME", str(tmp_path))
    monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
    client = TestClient(build_app())
    assert client.get("/surface", headers=auth()).status_code == 503


def test_liveness_needs_no_token_and_leaks_nothing(client):
    body = client.get("/console/health").json()
    assert body["status"] == "ok"
    assert TOKEN not in json.dumps(body)


# ---------------------------------------------------------------------------
# /surface — the robot's CURRENT capabilities, not the ones in an old QR
# ---------------------------------------------------------------------------


def test_surface_is_exactly_the_projection_the_qr_carries(client, home):
    # One projection, one parser on the client. If these ever diverge, a phone
    # that refreshes gets a different-shaped robot than the one it paired with.
    from castor.pairing import capability_surface_from_manifest

    path = rc_car_manifest(home)
    body = client.get("/surface", headers=auth()).json()
    assert body["capability_surface"] == capability_surface_from_manifest(path)
    assert body["rrn"] == "RRN-LOCAL-abc123"


def test_surface_matches_the_qr_field_byte_for_byte_when_the_qr_fits(client, home):
    # The rc-car surface fits the QR budget whole, so the two are identical —
    # which is the case the iOS client is being written against.
    from castor.pairing import build_pair_payload, capability_surface_from_manifest

    path = rc_car_manifest(home)
    payload = build_pair_payload(
        gateway_url="http://192.0.2.5:8080", bearer="rmg_live_x",
        manifest_path=str(path), rrn="RRN-LOCAL-abc123",
        capability_surface=capability_surface_from_manifest(path),
    )
    http_surface = client.get("/surface", headers=auth()).json()["capability_surface"]
    assert http_surface == payload["capability_surface"]


def test_THEPOINT_surface_is_untrimmed_where_the_qr_had_to_drop_detail(client, home):
    # The QR has a byte budget because a QR too dense to scan pairs nothing, so
    # `_fit_surface` drops the contracts first. HTTP has no such budget: the
    # phone's FIRST refresh must get back everything the camera could not carry.
    from castor.pairing import (
        PAIR_QR_BYTE_BUDGET,
        build_pair_payload,
        capability_surface_from_manifest,
    )

    path = many_capability_manifest(home)
    full = capability_surface_from_manifest(path)
    payload = build_pair_payload(
        gateway_url="http://192.0.2.5:8080", bearer="rmg_live_x",
        manifest_path=str(path), rrn="RRN-000000000099",
        capability_surface=full,
    )
    assert len(json.dumps(payload, separators=(",", ":")).encode()) <= PAIR_QR_BYTE_BUDGET
    assert "contracts" not in payload["capability_surface"], (
        "fixture is too small — this test only means something when the QR trims")

    surface = client.get("/surface", headers=auth()).json()["capability_surface"]
    assert surface == full
    assert len(surface["capabilities"]) == 14
    assert len(surface["contracts"]) == 14
    assert surface["object_descriptors"] == ["red_block", "blue_block"]


def test_no_manifest_is_an_honest_404(client, home):
    response = client.get("/surface", headers=auth())
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ROBOT.md" in detail and str(home) in detail


def test_a_robot_that_declares_nothing_says_so_rather_than_erroring(client, home):
    # `capability_surface_from_manifest` returns None for a manifest with no
    # capabilities, and null is the honest answer: an empty surface would be a
    # claim of its own.
    (home / "ROBOT.md").write_text("---\nmetadata:\n  rrn: RRN-000000000001\n---\n")
    body = client.get("/surface", headers=auth()).json()
    assert body == {"capability_surface": None, "rrn": "RRN-000000000001"}


def test_THEBUG_a_garbled_frontmatter_is_a_422_that_names_the_line(client, home):
    # A hand-edited ROBOT.md that no longer parses used to reach the client as a
    # 500 — which reads as "the console is broken" and sends the operator to
    # journalctl for the console instead of to the line they just typed. The
    # `except (OSError, ValueError)` around the rrn read never caught it:
    # yaml.YAMLError is neither.
    (home / "ROBOT.md").write_text(
        "---\n"
        "metadata:\n"
        "  robot_name: testbot\n"
        "   rrn: RRN-000000000001\n"   # one space too many: unparseable
        "---\n\n# testbot\n"
    )
    response = client.get("/surface", headers=auth())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("ROBOT.md frontmatter unparseable: ")
    assert "frontmatter line 3" in detail, f"the operator needs the location: {detail}"
    assert "\n" not in detail, "one line, not PyYAML's whole report"


def test_an_unparseable_manifest_never_answers_as_a_robot_that_declares_nothing(client, home):
    # The failure mode this 422 replaces is worse than a 500 would have been:
    # `capability_surface_from_manifest` swallows YAMLError and returns None, so
    # had the rrn read been made lenient too, a robot with a broken manifest
    # would answer 200 with a null surface — indistinguishable from a robot that
    # genuinely declares nothing, and the phone would render it as such.
    from castor.pairing import capability_surface_from_manifest

    (home / "ROBOT.md").write_text("---\ncapabilities:\n  - [unclosed\n---\n")
    assert capability_surface_from_manifest(home / "ROBOT.md") is None
    assert client.get("/surface", headers=auth()).status_code == 422


def test_gaps_are_served_from_the_robot_home(client, home):
    (home / "gaps.json").write_text(json.dumps(
        {"v": 1, "gaps": [{"id": "x", "kind": "missing-package"}]}))
    assert client.get("/gaps", headers=auth()).json()["gaps"][0]["id"] == "x"


def test_absent_gaps_are_an_empty_answer_not_an_error(client):
    assert client.get("/gaps", headers=auth()).json() == {"v": 1, "gaps": []}


# ---------------------------------------------------------------------------
# The curated ladder — the part a port loses silently
# ---------------------------------------------------------------------------


def test_the_suggestion_ladder_survived_the_port(client, monkeypatch):
    from castor.console import models

    monkeypatch.setattr(models, "_ollama", lambda *a, **k: {"models": []})
    monkeypatch.setattr(models, "_total_ram_gb", lambda: 16.0)
    names = [s["name"] for s in
             client.get("/models/suggestions", headers=auth()).json()["suggestions"]]
    assert names == ["qwen3.5:2b", "gemma4:e2b", "qwen3.5:4b", "gemma4:e4b-it-qat",
                     "gemma3:4b", "nomic-embed-text", "gemma4:12b-it-qat"]


def test_the_vision_brain_and_the_memory_substrate_are_still_labelled(client, monkeypatch):
    # These two are not interchangeable rows in a list: one is the only local
    # model on this bench that can genuinely look at a photo, the other is not a
    # chat model at all. A first-timer picking by name alone gets this wrong.
    from castor.console import models

    monkeypatch.setattr(models, "_ollama", lambda *a, **k: {"models": []})
    by_name = {s["name"]: s for s in
               client.get("/models/suggestions", headers=auth()).json()["suggestions"]}
    assert "VISION" in by_name["gemma4:12b-it-qat"]["good_for"]
    assert "min/reply" in by_name["gemma4:12b-it-qat"]["good_for"], "the pace must stay stated"
    assert "Memory" in by_name["nomic-embed-text"]["good_for"]
    assert "not a chat model" in by_name["nomic-embed-text"]["good_for"]


def test_a_model_only_fits_if_it_leaves_the_robot_room_to_run(client, monkeypatch):
    # 60% of RAM, because the other 40% is the gateway, the runtime, and this
    # console. A model that fits with nothing else running is a model that gets
    # the robot OOM-killed mid-drive.
    from castor.console import models

    monkeypatch.setattr(models, "_ollama", lambda *a, **k: {"models": []})
    monkeypatch.setattr(models, "_total_ram_gb", lambda: 8.0)
    fits = {s["name"]: s["fits"] for s in
            client.get("/models/suggestions", headers=auth()).json()["suggestions"]}
    assert fits["qwen3.5:2b"] is True          # 2.5 <= 4.8
    assert fits["gemma4:e4b-it-qat"] is False  # 5.3 >  4.8
    assert fits["gemma4:12b-it-qat"] is False  # 9.0 >  4.8

    monkeypatch.setattr(models, "_total_ram_gb", lambda: 16.0)
    fits = {s["name"]: s["fits"] for s in
            client.get("/models/suggestions", headers=auth()).json()["suggestions"]}
    assert fits["gemma4:12b-it-qat"] is True   # 9.0 <= 9.6


def test_a_host_that_cannot_read_its_own_ram_offers_nothing_it_cannot_judge(client, monkeypatch):
    from castor.console import models

    monkeypatch.setattr(models, "_ollama", lambda *a, **k: {"models": []})
    monkeypatch.setattr(models, "_total_ram_gb", lambda: 0.0)
    body = client.get("/models/suggestions", headers=auth()).json()
    assert body["host_ram_gb"] == 0.0
    assert all(s["fits"] is False for s in body["suggestions"])


def test_THEBUG_an_installed_model_is_not_offered_as_a_download(client, monkeypatch):
    # Ollama reports "nomic-embed-text:latest" for a pull of "nomic-embed-text".
    # An exact match marked an installed model as missing and offered the user a
    # download they had already waited through.
    from castor.console import models

    monkeypatch.setattr(models, "_ollama", lambda *a, **k: {
        "models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen3.5:2b"}]})
    by_name = {s["name"]: s for s in
               client.get("/models/suggestions", headers=auth()).json()["suggestions"]}
    assert by_name["nomic-embed-text"]["installed"] is True
    assert by_name["qwen3.5:2b"]["installed"] is True
    assert by_name["gemma3:4b"]["installed"] is False


# ---------------------------------------------------------------------------
# The brains — the invariant that costs money when it breaks
# ---------------------------------------------------------------------------


def fake_claude(home: Path) -> Path:
    exe = home / "claude"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return exe


def test_THEINVARIANT_the_claude_child_never_inherits_the_metered_key(home, monkeypatch):
    # ANTHROPIC_API_KEY in the ambient environment (this bench has had one in
    # /etc/environment for months) silently converts every turn from "the
    # subscription the operator already pays for" to per-token billing — and the
    # answer looks identical, so nothing surfaces it but the invoice.
    from castor.console import brains

    monkeypatch.setenv("CLAUDE_CLI", str(fake_claude(home)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-scratch-value-not-a-key")
    monkeypatch.setenv("CASTOR_CONSOLE_MARKER", "kept")
    seen: dict = {}

    def record(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "hello"}), "")

    monkeypatch.setattr(brains.subprocess, "run", record)
    out = brains.anthropic_chat("be brief", "hi", [])

    env = seen["kwargs"]["env"]
    assert brains.METERED_KEY_ENV == "ANTHROPIC_API_KEY"
    assert "ANTHROPIC_API_KEY" not in env, "the child would bill the metered key"
    assert env["CASTOR_CONSOLE_MARKER"] == "kept", "only that one key is removed"
    assert out["content"] == "hello"


def test_the_subscription_brain_answers_a_chat_turn(client, home, monkeypatch):
    # The wiring, not the CLI: active-model says anthropic-sub, so /models/chat
    # must route there instead of asking Ollama for a model named 'claude'.
    from castor.console import brains, models

    (home / "active-model.json").write_text(json.dumps(
        {"provider": "anthropic-sub", "model": "claude"}))
    monkeypatch.setattr(brains, "anthropic_chat",
                        lambda *a, **k: {"content": "on it", "thinking": ""})
    monkeypatch.setattr(models, "_ollama", _refuse_ollama)
    body = client.post("/models/chat", headers=auth(),
                       json={"message": "hello"}).json()
    assert body["model"] == "claude (subscription)"
    assert body["content"] == "on it"


def _refuse_ollama(*args, **kwargs):
    raise AssertionError("this turn must not reach Ollama")


def test_a_photo_reaches_the_local_model_instead_of_being_dropped(client, home, monkeypatch):
    # Before local vision landed, an attached photo was SILENTLY DROPPED on the
    # Ollama path: the model answered about a picture it never saw, in whatever
    # words made that sound plausible. Ollama takes base64 in `images`.
    from castor.console import models

    (home / "active-model.json").write_text(json.dumps(
        {"provider": "ollama", "model": "gemma4:12b-it-qat"}))
    sent: dict = {}

    def capture(path, payload=None, timeout=15.0, base=None):
        sent["path"], sent["payload"], sent["base"] = path, payload, base
        return {"message": {"content": "a red block"}}

    monkeypatch.setattr(models, "_ollama", capture)
    client.post("/models/chat", headers=auth(),
                json={"message": "what is this?", "image_b64": "/9j/scratch"})
    assert sent["payload"]["messages"][-1]["images"] == ["/9j/scratch"]
    # Straight to Ollama unless an operator points CHAT_UPSTREAM at a recorder.
    assert sent["base"] == "http://127.0.0.1:11434"


def test_THEPROMISE_a_local_vision_turn_cannot_ride_the_subscription_off_lan(
        client, home, monkeypatch):
    # The app's per-robot Vision pick promises "nothing leaves your network".
    # With the ACTIVE provider set to the Claude subscription, a per-request
    # model override alone would ride the active branch — the frame off-LAN
    # under a local label. `provider` forces the branch for ONE turn.
    from castor.console import brains, models

    (home / "active-model.json").write_text(json.dumps(
        {"provider": "anthropic-sub", "model": "claude"}))
    monkeypatch.setattr(brains, "anthropic_chat", _refuse_claude)
    sent: dict = {}

    def capture(path, payload=None, timeout=15.0, base=None):
        sent["payload"] = payload
        return {"message": {"content": "a doorway"}}

    monkeypatch.setattr(models, "_ollama", capture)
    body = client.post("/models/chat", headers=auth(), json={
        "message": "what is this?", "image_b64": "/9j/scratch",
        "provider": "ollama", "model": "gemma4:12b-it-qat"}).json()
    assert body["model"] == "gemma4:12b-it-qat"
    assert sent["payload"]["model"] == "gemma4:12b-it-qat"
    assert sent["payload"]["messages"][-1]["images"] == ["/9j/scratch"]
    # One turn, not a mode change: the active brain is exactly as it was.
    active = json.loads((home / "active-model.json").read_text())
    assert active["provider"] == "anthropic-sub"


def _refuse_claude(*args, **kwargs):
    raise AssertionError("a forced-local turn must not reach the subscription brain")


def test_an_unknown_provider_override_is_refused_not_guessed(client, home):
    resp = client.post("/models/chat", headers=auth(),
                       json={"message": "hi", "provider": "openai"})
    assert resp.status_code == 422
    assert "unknown provider" in resp.json()["detail"]


def test_the_console_reports_configuration_never_key_material(client, home, monkeypatch):
    from castor.console import brains

    (home / "brain-secrets.env").write_text("GEMINI_API_KEY=AIza-scratch-value\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert brains.gemini_available() is True
    body = client.get("/models/auth", headers=auth())
    assert "AIza-scratch-value" not in body.text
    assert body.json()["gemini_er"]["configured"] is True


# ---------------------------------------------------------------------------
# `castor up` ships it — the unit, and a token that is never rotated
# ---------------------------------------------------------------------------


def test_up_writes_a_console_unit_on_the_ports_it_already_reserved():
    from castor.up import UpPlan, unit_files

    plan = UpPlan(name="testbot", home=Path("/home/pi/testbot"), archetype="rc-car",
                  rrn="RRN-LOCAL-abc123", robot_uuid="u-1", base_port=9000)
    unit = unit_files(plan, python="/venv/bin/python",
                      gateway_bin="/venv/bin/robot-md-gateway")["testbot-console.service"]
    assert "ExecStart=/venv/bin/python -m castor.console" in unit
    assert "Environment=CONSOLE_PORT=9002" in unit
    assert "Environment=ROBOT_HOME=/home/pi/testbot" in unit
    assert "EnvironmentFile=/home/pi/testbot/console.env" in unit


def test_THERULE_the_console_token_is_generated_once_and_never_rotated(tmp_path):
    # Same reuse-don't-refuse contract as the gateway bearers, for the same
    # reason: this token rides in the pairing QR, and the most common reason to
    # rerun `up` is a stale QR. Rotating the credential inside the thing you are
    # regenerating un-pairs every phone that ever scanned one.
    from castor.up import ensure_console_token

    first, reused = ensure_console_token(tmp_path)
    assert reused is False and first.startswith("oc_console_")
    again, reused = ensure_console_token(tmp_path)
    assert (again, reused) == (first, True)

    # And the reuse path RE-ASSERTS the mode. The file `up` finds on a rerun is
    # not always the file `up` wrote: restored from a backup, or copied with
    # `cp` (which takes the umask, not the source mode). Only the generate path
    # used to chmod, so a live console bearer stayed world-readable through
    # every subsequent successful `up`.
    (tmp_path / "console.env").chmod(0o644)
    again, reused = ensure_console_token(tmp_path)
    assert (again, reused) == (first, True)
    assert oct((tmp_path / "console.env").stat().st_mode & 0o777) == "0o600"


def test_the_console_token_file_is_not_world_readable(tmp_path):
    from castor.up import ensure_console_token

    ensure_console_token(tmp_path)
    assert oct((tmp_path / "console.env").stat().st_mode & 0o777) == "0o600"


def test_a_hand_edited_console_env_keeps_its_other_lines(tmp_path):
    from castor.up import ensure_console_token

    env = tmp_path / "console.env"
    env.write_text("CHAT_UPSTREAM=http://127.0.0.1:4141\n")
    token, reused = ensure_console_token(tmp_path)
    assert reused is False
    text = env.read_text()
    assert "CHAT_UPSTREAM=http://127.0.0.1:4141" in text
    assert f"CONSOLE_TOKEN={token}" in text


def stub_the_host(monkeypatch, tmp_path) -> None:
    """Let `up` run for real without touching this machine.

    HOME is redirected (so systemd units land in the scratch tree), the bus scan
    and the port probes are stubbed out, and gaps collection is skipped. A test
    must not enumerate the operator's I2C bus or poke a live service to prove
    that a file got written.
    """
    import castor.up as up

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(up, "detect_brain", lambda: ("ollama", ""))
    monkeypatch.setattr(up, "_port_answers", lambda port: False)
    monkeypatch.setattr("castor.peripherals.scan_i2c", lambda: [])
    monkeypatch.setattr("castor.gaps.collect", lambda **kwargs: [])


def test_up_pairs_the_phone_to_the_console_and_a_rerun_keeps_it(tmp_path, monkeypatch):
    """The end of the ten-minute path: scan once, get the brains too.

    What is asserted is the composition: the unit exists, and the QR carries the
    same console the unit serves.
    """
    import castor.up as up

    stub_the_host(monkeypatch, tmp_path)

    home = tmp_path / "testbot"
    plan = up.run_up(home=home, base_port=8300, python=sys.executable,
                     start_services=False)

    unit = tmp_path / ".config" / "systemd" / "user" / "testbot-console.service"
    assert f"CONSOLE_PORT={plan.console_port}" in unit.read_text()

    payload = json.loads((home / "pair-payload.json").read_text())
    token, _ = up.ensure_console_token(home)
    assert payload["console_token"] == token
    assert payload["console_url"].endswith(f":{plan.console_port}")
    # The QR must never carry the console token where the actuate bearer goes.
    assert payload["console_token"] != payload["bearer"]

    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)
    rerun = json.loads((home / "pair-payload.json").read_text())
    assert rerun["console_token"] == token, "a rerun un-paired the phone"


def test_THEBUG_a_pair_rerun_on_an_up_host_keeps_the_console_fields(tmp_path, monkeypatch):
    """`castor pair` on a `castor up` host must not strip the brains back out.

    `up` persists the console bearer in exactly ONE place — <home>/console.env,
    read by the unit — and nothing exports it into an interactive shell. So the
    most ordinary rerun there is (`castor pair` after the Pi's DHCP address
    moved) rebuilt the QR with neither console_url nor console_token, and the
    freshly-scanned phone came back with no brains and no /surface, silently.
    """
    import types

    import castor.up as up
    from castor.cli import cmd_pair

    stub_the_host(monkeypatch, tmp_path)

    home = tmp_path / "testbot"
    plan = up.run_up(home=home, base_port=8300, python=sys.executable,
                     start_services=False)
    token, _ = up.ensure_console_token(home)

    # No flag, and nothing in the environment: console.env is the only source.
    monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
    out_dir = tmp_path / "out"
    args = types.SimpleNamespace(
        manifest_path=str(home / "ROBOT.md"),
        gateway_url=f"http://192.0.2.5:{plan.gateway_port}",
        port=plan.gateway_port,
        bearer=None, bearers=str(home / "bearers.yaml"), rrn=None,
        estop_url=None,
        console_url=None, console_port=None, console_token=None,
        out_dir=str(out_dir),
        key_file=str(tmp_path / "attest.pem"),
        env_file=str(tmp_path / "attest.env"),
        kid=None, force=False,
    )
    assert cmd_pair(args) == 0

    payload = json.loads((out_dir / "pair-payload.json").read_text())
    assert payload["console_token"] == token
    # Derived the way `up` derives it: the gateway's own host, base_port + 2.
    assert payload["console_url"] == f"http://192.0.2.5:{plan.console_port}"
    assert payload["console_token"] != payload["bearer"]


def test_a_hand_set_console_port_in_console_env_is_what_pair_advertises(tmp_path, monkeypatch):
    # console.env is the file whose header invites hand edits, and the unit now
    # applies it after the generated defaults — so an operator who moves the
    # port there has really moved it, and the QR must agree.
    import types

    import castor.up as up
    from castor.cli import cmd_pair

    stub_the_host(monkeypatch, tmp_path)
    home = tmp_path / "testbot"
    up.run_up(home=home, base_port=8300, python=sys.executable, start_services=False)
    with (home / "console.env").open("a") as env:
        env.write("CONSOLE_PORT=9111\n")

    monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
    out_dir = tmp_path / "out"
    cmd_pair(types.SimpleNamespace(
        manifest_path=str(home / "ROBOT.md"),
        gateway_url="http://192.0.2.5:8300", port=8300,
        bearer=None, bearers=str(home / "bearers.yaml"), rrn=None,
        estop_url=None,
        console_url=None, console_port=None, console_token=None,
        out_dir=str(out_dir),
        key_file=str(tmp_path / "attest.pem"),
        env_file=str(tmp_path / "attest.env"),
        kid=None, force=False,
    ))
    payload = json.loads((out_dir / "pair-payload.json").read_text())
    assert payload["console_url"] == "http://192.0.2.5:9111"


def test_the_console_does_not_log_the_token_it_accepts_in_a_query_string(home, monkeypatch):
    # The ?token= form exists so an <img src> can carry the bearer; uvicorn's
    # access log would then write that credential into journald, where it is
    # readable by the systemd-journal group and copied verbatim into every
    # `journalctl` paste in a bug report.
    import types

    from castor.console import __main__ as entrypoint

    seen: dict = {}
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda app, **kwargs: seen.update(kwargs)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    entrypoint.main()
    assert seen["access_log"] is False


def test_the_console_serves_the_robot_up_just_generated(tmp_path, monkeypatch):
    """One end-to-end seam: what `up` writes is what /surface reads.

    The two halves of #934 only mean something together — an endpoint serving a
    manifest nobody generates, or a manifest no endpoint serves, is the state
    this work replaced.
    """
    import castor.up as up
    from castor.console.app import build_app
    from castor.pairing import capability_surface_from_manifest

    stub_the_host(monkeypatch, tmp_path)

    robot = tmp_path / "testbot"
    plan = up.run_up(home=robot, base_port=8300, python=sys.executable,
                     start_services=False)
    token, _ = up.ensure_console_token(robot)

    monkeypatch.setenv("ROBOT_HOME", str(robot))
    monkeypatch.setenv("CONSOLE_TOKEN", token)
    client = TestClient(build_app())
    body = client.get("/surface", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["rrn"] == plan.rrn
    assert body["capability_surface"] == capability_surface_from_manifest(
        robot / "ROBOT.md")
    assert "drive.stop" in body["capability_surface"]["capabilities"]


def test_the_ported_console_reads_its_home_late_not_at_import(tmp_path, monkeypatch):
    # The bench modules captured ROBOT_HOME at import, which is why a second
    # robot on the same host was a fork rather than a second unit.
    from castor.console import config

    monkeypatch.setenv("ROBOT_HOME", str(tmp_path / "one"))
    assert config.robot_home().name == "one"
    monkeypatch.setenv("ROBOT_HOME", str(tmp_path / "two"))
    assert config.robot_home().name == "two"
    assert config.manifest_path() == tmp_path / "two" / "ROBOT.md"


def test_the_model_daemon_address_is_a_setting_not_a_constant(home, monkeypatch):
    # It was a module constant, in a module whose own docstring says every
    # setting is read at call time. A robot whose weights live on the
    # workstation beside it could not say so without editing the package.
    from castor.console import config

    assert config.ollama_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://192.0.2.20:11434")
    assert config.ollama_url() == "http://192.0.2.20:11434"
    # And chat follows it, unless the operator has separately named a recorder.
    assert config.chat_upstream() == "http://192.0.2.20:11434"
    monkeypatch.setenv("CHAT_UPSTREAM", "http://127.0.0.1:4141")
    assert config.chat_upstream() == "http://127.0.0.1:4141"


def test_every_ollama_call_follows_the_moved_daemon(client, home, monkeypatch):
    # Management (tags/ps/pull) and chat both. A pull that still went to
    # loopback while chat went to the workstation would download weights onto
    # the machine that is not running the model.
    from castor.console import models

    monkeypatch.setenv("OLLAMA_URL", "http://192.0.2.20:11434")
    seen: list[str] = []

    class FakeResponse:
        def read(self):
            return b'{"models": []}'

        def __iter__(self):  # the pull worker streams progress lines
            return iter(())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def capture(req, timeout=None):
        seen.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr(models.urllib.request, "urlopen", capture)
    client.get("/models/local", headers=auth())
    try:
        models._pull_worker("qwen3.5:2b")
    finally:
        models.pull_state.status = "idle"
    assert seen[0] == "http://192.0.2.20:11434/api/tags"
    assert "http://192.0.2.20:11434/api/pull" in seen


def test_chat_goes_straight_to_ollama_unless_an_operator_says_otherwise(monkeypatch):
    # The bench default was a recording proxy on :4141. That proxy is a bench
    # extra, not something pip brings, and defaulting to a port nothing listens
    # on turns every first chat on a fresh host into a 504.
    from castor.console import config

    monkeypatch.delenv("CHAT_UPSTREAM", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert config.chat_upstream() == "http://127.0.0.1:11434"
    monkeypatch.setenv("CHAT_UPSTREAM", "http://127.0.0.1:4141")
    assert config.chat_upstream() == "http://127.0.0.1:4141"
