"""castor up — one command from bare host to paired robot.

THE TEN-MINUTE CONTRACT. OpenCastor's goal is a robot up and running in under
ten minutes, on common hardware, by people who do not administer Linux for a
living. This bench is the counter-example that motivated this file: the rover
took an EXPERIENCED operator three sessions of hand work — hand-written systemd
units, hand-edited env files, a hand-assembled pairing payload that shipped
pinned to a dead DHCP address. Every piece existed; the composition did not.

`castor up` is that composition, and nothing else:

    detect hardware -> pick archetype -> generate the robot home
    -> sign the manifest -> start the services -> print the pairing QR

Design rules, each earned the hard way:

  * NON-INTERACTIVE. The wizard already covers Q&A. A first-timer cannot
    answer questions about tool tiers and oscillator trims; defaults must be
    the safe answers, and every question this could ask is one it can answer
    itself by looking at the machine.
  * IDEMPOTENT. Rerunning `up` on a configured robot refreshes what is stale
    (the QR, the service files) and REUSES what is identity (keys, tokens,
    RRN) — the same reuse-don't-refuse contract `castor pair` learned after a
    --force rotated a live signing key to change an IP address.
  * SAFE BY DEFAULT. Detecting a PCA9685 selects the rc-car archetype but the
    generated config drives SIMULATED wheels: real PWM stays a deliberate,
    documented flip AFTER the on-stand checks, never a side effect of setup.
    An `up` that could make hardware move was rejected outright.
  * TEMPLATES ARE THE PROVEN FILES. The rc-car home is generated from the
    actual configs this bench runs — the ones the 27-check smoke suite passes
    against — with identity substituted. Fresh prose in a generator drifts
    from reality; a template cut from a working robot cannot.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

logger = logging.getLogger("castor.up")

ARCHETYPES = ("rc-car", "sim")

#: Port layout relative to --base-port: one robot is three adjacent services.
GATEWAY_OFF, RUNTIME_OFF, CONSOLE_OFF = 0, 1, 2
RRF_STUB_PORT = 8090


@dataclass
class UpPlan:
    """Everything `up` decided, before it touches the filesystem."""

    name: str
    home: Path
    archetype: str
    rrn: str
    robot_uuid: str
    base_port: int
    detected: list[str] = field(default_factory=list)

    @property
    def gateway_port(self) -> int:
        return self.base_port + GATEWAY_OFF

    @property
    def runtime_port(self) -> int:
        return self.base_port + RUNTIME_OFF

    @property
    def console_port(self) -> int:
        return self.base_port + CONSOLE_OFF


# ---------------------------------------------------------------------------
# Decisions (pure, testable)
# ---------------------------------------------------------------------------


def pick_archetype(i2c_addresses: set[int]) -> tuple[str, list[str]]:
    """Choose an archetype from what the bus scan actually found.

    Detection selects the SHAPE of the robot, never whether it can move: an
    rc-car archetype still starts on simulated wheels. 0x40 is the PCA9685's
    default address — the one every hat and breakout ships at.
    """
    found: list[str] = []
    if 0x40 in i2c_addresses:
        found.append("PCA9685 PWM controller at 0x40 (i2c)")
        return "rc-car", found
    return "sim", found


def derive_identity(name: str) -> tuple[str, str]:
    """A locally-derived RRN and uuid for a robot not yet registered.

    Honest about what it is: `RRN-LOCAL-...` cannot be mistaken for a
    registry-issued number, and `castor register` upgrades it later. Derived
    from a random uuid rather than the name so two robots that are both
    called "robot" do not collide.
    """
    robot_uuid = str(uuid.uuid4())
    rrn = f"RRN-LOCAL-{robot_uuid.replace('-', '')[:10]}"
    return rrn, robot_uuid


def render(template_name: str, plan: UpPlan, **extra: str) -> str:
    """Fill one packaged template. Placeholders are {name}-style."""
    text = (resources.files("castor") / "templates" / "rc_car" / template_name).read_text()
    mapping = {
        "name": plan.name,
        "rrn": plan.rrn,
        "uuid": plan.robot_uuid,
        "port_runtime": str(plan.runtime_port),
        **extra,
    }
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text


def unit_files(plan: UpPlan, *, python: str, gateway_bin: str) -> dict[str, str]:
    """The systemd user units, rendered from the shapes this bench runs."""
    home, name = plan.home, plan.name
    units = {}
    units[f"{name}-gateway.service"] = f"""[Unit]
Description=robot-md-gateway for {name} — /v1/invoke with Ed25519-signed receipts
After=network-online.target

[Service]
EnvironmentFile={home}/gateway-attestation.env
EnvironmentFile={home}/gateway-policy.env
Environment=ROBOT_MANIFEST={home}/ROBOT.md
Environment=OPENCASTOR_OPS_RRF_URL=http://127.0.0.1:{RRF_STUB_PORT}
ExecStart={gateway_bin} serve --host 0.0.0.0 --port {plan.gateway_port} --bearers {home}/bearers.yaml --robot-md {home}/ROBOT.md
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    units[f"{name}-castor.service"] = f"""[Unit]
Description=OpenCastor runtime for {name} — /health /api/stop /ws/telemetry
After=network-online.target

[Service]
EnvironmentFile={home}/tokens.env
Environment=ROBOT_HOME={home}
Environment=ROBOT_NAME={name}
Environment=ROBOT_GATEWAY_URL=http://127.0.0.1:{plan.gateway_port}
Environment=ROBOT_MANIFEST={home}/ROBOT.md
Environment=ROBOT_RUNTIME_PORT={plan.runtime_port}
Environment=OPENCASTOR_CONFIG={home}/robot.rcan.yaml
ExecStart={python} {home}/runtime.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    units[f"{name}-rrf-stub.service"] = f"""[Unit]
Description=RRF key resolver stub for {name} (loopback kid lookup)

[Service]
Environment=RRF_KEY_DIR={home}/keys/rrf
Environment=RRF_PORT={RRF_STUB_PORT}
ExecStart={python} -m castor.rrf_stub
Restart=on-failure

[Install]
WantedBy=default.target
"""
    return units


# ---------------------------------------------------------------------------
# Manifest signing
# ---------------------------------------------------------------------------


def sign_manifest(body: str, key_file: Path, kid: str) -> str:
    """Sign ROBOT.md the way the gateway verifies it: Ed25519 over the body,
    a ROBOT-MD-SIG footer, and the kid resolvable through the RRF stub."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if key_file.exists():
        priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    else:
        priv = Ed25519PrivateKey.generate()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
        key_file.chmod(0o600)
    # SIGN EXACTLY THE BYTES THE GATEWAY WILL VERIFY. Its footer regex starts
    # at the newline BEFORE the comment, and it verifies text[:match.start()] —
    # i.e. the body WITHOUT that final newline. Signing the body with the
    # newline produced a signature that verified beautifully in a bare test
    # and failed as `manifest_provenance` on the live gateway: one byte of
    # framing, two honest implementations, no error message that names it.
    canonical = body.rstrip("\n")
    sig = base64.b64encode(priv.sign(canonical.encode("utf-8"))).decode()
    return f"{canonical}\n<!-- ROBOT-MD-SIG kid={kid} sig={sig} -->\n"


def publish_manifest_key(key_file: Path, kid: str, rrf_dir: Path) -> Path:
    """Drop the verify key where the stub serves it."""
    from cryptography.hazmat.primitives import serialization

    priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    rrf_dir.mkdir(parents=True, exist_ok=True)
    out = rrf_dir / f"{kid}.pem"
    out.write_bytes(pub_pem)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _say(step: str, started: float) -> None:
    print(f"  [{time.monotonic() - started:5.1f}s] {step}")


def run_up(*, home: Path, name: str | None = None, archetype: str | None = None,
           base_port: int = 8080, python: str | None = None,
           start_services: bool = True) -> UpPlan:
    """The whole bring-up. Prints progress; returns the plan for callers/tests."""
    started = time.monotonic()
    home = home.expanduser().resolve()
    name = name or home.name

    # -- detect ------------------------------------------------------------
    i2c: set[int] = set()
    try:
        from castor.peripherals import scan_i2c
        i2c = {p.i2c_address for p in scan_i2c() if p.i2c_address is not None}
    except Exception:  # noqa: BLE001 - no bus is a valid machine state
        pass
    picked, detected = pick_archetype(i2c)
    archetype = archetype or picked
    if archetype not in ARCHETYPES:
        raise SystemExit(f"unknown archetype {archetype!r} (know: {ARCHETYPES})")
    for line in detected:
        _say(f"detected: {line}", started)
    _say(f"archetype: {archetype}", started)

    # -- identity (reused on rerun, generated once) -------------------------
    state_file = home / ".castor-up.json"
    if state_file.exists():
        state = json.loads(state_file.read_text())
        rrn, robot_uuid = state["rrn"], state["uuid"]
        _say("identity: reused existing", started)
    else:
        rrn, robot_uuid = derive_identity(name)
        _say(f"identity: {rrn} (local — `castor register` upgrades it)", started)

    plan = UpPlan(name=name, home=home, archetype=archetype, rrn=rrn,
                  robot_uuid=robot_uuid, base_port=base_port, detected=detected)

    # -- home dir ------------------------------------------------------------
    home.mkdir(parents=True, exist_ok=True)
    (home / "keys" / "rrf").mkdir(parents=True, exist_ok=True)

    manifest_kid = f"{name}-manifest"
    manifest_key = home / "keys" / "manifest-ed25519-private.pem"
    body = render("ROBOT.md.tmpl", plan)
    (home / "ROBOT.md").write_text(sign_manifest(body, manifest_key, manifest_kid))
    _say(f"ROBOT.md written and signed (kid {manifest_kid})", started)

    (home / "robot.rcan.yaml").write_text(render("robot.rcan.yaml.tmpl", plan))
    policy = home / "gateway-policy.env"
    if not policy.exists():
        # Never overwritten: this file is where an operator later flips
        # simulated wheels to real ones, and a rerun of `up` must not
        # silently reverse that decision — or make it.
        policy.write_text(render("gateway-policy.env.tmpl", plan))
    (home / "runtime.py").write_text(render("runtime.py.tmpl", plan))

    # -- bearers + runtime tokens (reused: rotating them un-pairs the phone) --
    bearers = home / "bearers.yaml"
    if bearers.exists():
        from castor.pairing import read_bearer_from_bearers_yaml
        actuate = read_bearer_from_bearers_yaml(bearers)
        read_tok = read_bearer_from_bearers_yaml(bearers, prefer_tier="read")
        _say("bearers: tokens reused", started)
    else:
        actuate = f"rmg_live_{secrets.token_hex(16)}"
        read_tok = f"rmg_read_{secrets.token_hex(16)}"
        _say("bearers: tokens generated (actuate + read)", started)
    # TOKENS are identity and reused (rotating them un-pairs the phone); the
    # ACTUATOR section is re-resolved every run against what is actually
    # installed — so `pip install rc-car-actuator` followed by a rerun
    # upgrades noop -> rc-car without touching the pairing.
    #
    # `caller`, not `name`: the field names the audit trail's actor and the
    # gateway KeyErrors on anything else — caught live when the first scratch
    # robot's gateway crash-looped on exactly this. rc-car with an empty
    # config is the SIMULATED-wheels default — real PWM is a deliberate later
    # flip in gateway-policy.env, never a setup default.
    actuator_name, actuator_note = resolve_actuator()
    bearers.write_text(
        "# robot-md-gateway bearers — generated by `castor up`.\n"
        "bearers:\n"
        f"  - token: {actuate}\n    tier: actuate\n    caller: {name}-phone\n"
        f"  - token: {read_tok}\n    tier: read\n    caller: {name}-runtime\n"
        "actuator:\n"
        f"  name: {actuator_name}\n"
        "  config: {}\n")
    bearers.chmod(0o600)
    if actuator_note:
        _say(f"actuator: {actuator_name} — {actuator_note}", started)
    else:
        _say(f"actuator: {actuator_name} (simulated wheels)", started)
    tokens = home / "tokens.env"
    if not tokens.exists():
        # OPENCASTOR_API_TOKEN guards the runtime's own /api endpoints; the
        # runtime REFUSES TO START without it (fail closed, correctly). Its
        # own token, not the gateway read bearer: leaking a camera URL must
        # not also hand out the runtime's stop endpoint.
        tokens.write_text(
            f"ACTUATE_TOKEN={actuate}\nREAD_TOKEN={read_tok}\n"
            f"OPENCASTOR_API_TOKEN=oc_api_{secrets.token_hex(16)}\n")
        tokens.chmod(0o600)

    # -- rrf stub key + attestation identity --------------------------------
    stub_dir = home / "keys" / "rrf"
    publish_manifest_key(manifest_key, manifest_kid, stub_dir)

    from castor.pairing import generate_attestation_identity, set_env_var
    attest_key = home / "keys" / "attestation-ed25519-private.pem"
    identity = generate_attestation_identity(attest_key, kid=f"{name}-gw-attest")
    env_file = home / "gateway-attestation.env"
    set_env_var(env_file, "ROBOT_MD_ATTESTATION_KEY_FILE", str(identity.key_file))
    set_env_var(env_file, "ROBOT_MD_ATTESTATION_KID", identity.kid)
    # The gateway's receipts must also resolve, same stub.
    publish_manifest_key(attest_key, identity.kid, stub_dir)
    _say(f"attestation: {identity.kid}", started)

    # -- AI models -----------------------------------------------------------
    provider, model = detect_brain()
    (home / "active-model.json").write_text(json.dumps(
        {"provider": provider, "model": model, "updated_at": time.time()}, indent=2))
    _say(f"brain: {provider} {model or '(subscription)'}".rstrip(), started)

    state_file.write_text(json.dumps({"rrn": rrn, "uuid": robot_uuid,
                                      "archetype": archetype,
                                      "base_port": base_port}, indent=2))

    # -- services ------------------------------------------------------------
    python = python or shutil.which("python3") or "/usr/bin/python3"
    # The gateway NEXT TO the chosen python first: `which` can find a stale
    # copy on PATH (a ~/.local/bin shim did exactly that on this bench) while
    # the real one lives in the venv the services run from.
    sibling = Path(python).parent / "robot-md-gateway"
    gateway_bin = str(sibling) if sibling.exists() else shutil.which("robot-md-gateway")
    if not gateway_bin:
        # Writing a unit for a binary that is not there produces a crash-loop
        # a beginner has to diagnose through journalctl. Failing HERE, with
        # the fix in the message, is the ten-minute behaviour.
        raise SystemExit(
            "robot-md-gateway is not installed in this environment.\n"
            "It is a dependency of opencastor — `pip install opencastor` "
            "(or `pip install robot-md-gateway`), then rerun `castor up`.")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    rendered = unit_files(plan, python=python, gateway_bin=gateway_bin)

    stub_running = _port_answers(RRF_STUB_PORT)
    if stub_running:
        # Another robot's stub already serves :8090 — publish into ITS key dir
        # too, if we can find it, rather than fighting over the port.
        rendered.pop(f"{name}-rrf-stub.service")
        for candidate in (Path.home() / "bob" / "keys" / "rrf",):
            if candidate.is_dir():
                publish_manifest_key(manifest_key, manifest_kid, candidate)
                publish_manifest_key(attest_key, identity.kid, candidate)
                _say(f"rrf stub: reusing :{RRF_STUB_PORT}, keys published to {candidate}", started)
                break
    for unit_name, content in rendered.items():
        (unit_dir / unit_name).write_text(content)
    _say(f"services written: {', '.join(rendered)}", started)

    if start_services:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for unit_name in rendered:
            subprocess.run(["systemctl", "--user", "enable", "--now", unit_name],
                           check=True, capture_output=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _port_answers(plan.runtime_port):
                break
            time.sleep(0.5)
        _say("services started", started)

    # -- pair ----------------------------------------------------------------
    from castor.pairing import build_pair_payload, capability_surface_from_manifest, \
        default_gateway_url, write_pair_artifacts

    gateway_url = default_gateway_url(port=plan.gateway_port)
    host = gateway_url.split("//")[1].rsplit(":", 1)[0]
    payload = build_pair_payload(
        gateway_url=gateway_url,
        bearer=actuate,
        manifest_path=str(home / "ROBOT.md"),
        rrn=rrn,
        estop_url=f"http://{host}:{plan.runtime_port}/api/stop",
        attest_kid=identity.kid,
        attest_pub=base64.b64encode(
            _spki_der(identity.pub_file)).decode(),
        capability_surface=capability_surface_from_manifest(home / "ROBOT.md"),
    )
    write_pair_artifacts(payload, home)
    _say(f"pairing QR: {home / 'pair-qr.png'}", started)

    # -- gaps: what this host's hardware could do that its software can't yet.
    # Data, not log lines — the app renders it, an AI can read it, and closing
    # one is always an operator-gated act (docs/SKILL-GAPS.md).
    from castor.gaps import collect, write as write_gaps
    found_gaps = collect(home=home)
    if found_gaps:
        write_gaps(found_gaps, home)
        _say(f"gaps: {len(found_gaps)} noted in gaps.json "
             f"({', '.join(g.kind for g in found_gaps)})", started)
    else:
        write_gaps([], home)
        _say("gaps: none — everything detected has a driver and a brain", started)
    print(f"\nDone in {time.monotonic() - started:.0f}s. "
          f"Scan {home / 'pair-qr.png'} with the OpenCastor app, "
          "then follow “Run your first drive”.")
    return plan


def resolve_actuator() -> tuple[str, str | None]:
    """Which gateway actuator this host can actually construct.

    `rc-car-actuator` is a separate package and — as of this writing — not on
    PyPI, so a fresh `pip install opencastor` does not have it. Writing
    `actuator: rc-car` anyway would crash-loop the gateway on an entry-point
    error no beginner can parse, at minute two of the ten minutes. The
    gateway's built-in `noop` actuator keeps the whole signed path alive —
    receipts, tiers, allowlists — with nothing to move, and the returned note
    tells the operator the one command that upgrades it.
    """
    try:
        from importlib.metadata import entry_points
        names = {ep.name for ep in entry_points(group="robot_md_gateway.actuators")}
    except Exception:  # noqa: BLE001
        names = set()
    if "rc-car" in names:
        return "rc-car", None
    return "noop", ("the rc-car actuator is not installed — "
                    "`pip install rc-car-actuator`, then rerun `castor up`")


def detect_brain() -> tuple[str, str]:
    """Local-first: the smallest Ollama model, else the Claude subscription,
    else Ollama-with-no-model (the console explains how to pull one)."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            models = json.load(r).get("models", [])
        if models:
            smallest = min(models, key=lambda m: m.get("size", 0))
            return "ollama", smallest["name"]
    except Exception:  # noqa: BLE001
        pass
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return "anthropic-sub", ""
    return "ollama", ""


def _spki_der(pub_file: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(pub_file.read_bytes())
    return pub.public_bytes(encoding=serialization.Encoding.DER,
                            format=serialization.PublicFormat.SubjectPublicKeyInfo)


def _port_answers(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0
