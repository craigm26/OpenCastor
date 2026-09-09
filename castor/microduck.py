"""Zero-config setup for the Pollen Robotics Microduck.

The goal of this module is one command — ``castor duck`` — that takes you from
a duck on your desk to a duck answering an LLM, with nothing typed in between.

It does four things, in order, and each one is independently useful:

1. **Find it.**  :func:`discover` checks, cheapest first: a local
   ``/run/robotd.sock`` (OpenCastor running on the duck itself), well-known
   hostnames starting with the stock image's own ``radxa-zero3.local``,
   ``duckctl ip`` over Bluetooth, and — with ``deep=True`` — an mDNS browse and
   the ARP neighbour table.  **A stock duck publishes no mDNS at all**, so mDNS
   is not on the default ladder and is not advertised as a way to find one; the
   address the operator already knows, ``--host``, is the reliable answer.
2. **Reach it.**  :func:`resolve_ssh_user` finds which account answers key auth,
   and :func:`check_robot_group` verifies that account can actually open the
   robotd socket.  Both failures have exact one-line fixes
   (:func:`ssh_copy_id_command`, :func:`robot_group_command`).
3. **Prove it.**  :func:`health` opens the real driver and calls ``robot.health``,
   so setup reports live loop rate and battery rather than "probably fine".
4. **Configure it.**  :func:`build_config` materialises the packaged
   ``pollen/microduck`` profile with the discovered host, and
   :func:`write_manifest` drops a ROBOT.md in ``~/.config/opencastor/`` — the
   format ``castor run`` accepts.  :func:`write_config` still writes the legacy
   ``.rcan.yaml`` for callers that want one, but nothing prints that path any
   more: ``castor run`` refuses a ``.rcan.yaml`` (``castor/cli.py:57-67``), and a
   setup command whose last line is a command that exits 1 is not a setup command.

Everything here is import-safe and side-effect free: no scanning happens until
you call a function.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from castor.drivers.microduck_driver import DEFAULT_SOCKET

logger = logging.getLogger("OpenCastor.Microduck")

__all__ = [
    "ALL_METHODS",
    "FAST_METHODS",
    "DuckCandidate",
    "PROFILE_ID",
    "discover",
    "verify",
    "health",
    "build_config",
    "write_config",
    "build_manifest",
    "write_manifest",
    "looks_like_duck_hostname",
    "resolve_ssh_user",
    "check_robot_group",
    "robot_group_command",
    "ssh_copy_id_command",
    "load_profile",
]

#: Profile id used by the wizard, ``suggest_preset()`` and ``castor duck``.
PROFILE_ID = "pollen/microduck"

#: Flat preset id (repo checkouts, ``castor setup`` numeric menu).
PRESET_ID = "pollen_microduck"

#: Hostnames worth trying before anything expensive, best-first.
#:
#: ``radxa-zero3.local`` comes first because it is what a **stock** duck answers to:
#: Pollen's ``docs/design/webrtc-console.md:27`` calls ``radxa-zero3`` "the hostname on
#: every board flashed from one image", so an out-of-the-box duck has that name and no
#: other.  ``duck-01`` is what Pollen's setup guide renames it to with
#: ``robotctl system set-name``, and the rest are names people pick.
#:
#: What cannot be guessed is ``configd``'s own default robot name: ``duck-<4 hex>``
#: derived from the SoC serial (``configd/src/identity.rs:84``).  That is 65536
#: hostnames, so it is matched (:func:`looks_like_duck_hostname`) rather than probed.
CANDIDATE_HOSTNAMES = (
    "radxa-zero3.local",
    "duck.local",
    "duck-01.local",
    "microduck.local",
    "duckling.local",
)

#: Hostname shapes that mean "this is probably a duck" when we meet one we did not
#: pick — an mDNS record, a reverse lookup, a name the operator typed.
DUCK_HOSTNAME_PREFIXES = ("duck", "microduck", "duckling", "radxa-zero3")


def looks_like_duck_hostname(name: str) -> bool:
    """True when *name* has the shape of a Microduck's hostname.

    Covers ``configd``'s default ``duck-<4 hex>`` (``configd/src/identity.rs:84``),
    the stock image's ``radxa-zero3`` and the names Pollen's guide suggests.  It is a
    hint for ordering candidates, never proof: :func:`verify` proves a duck by finding
    robotd's socket.
    """
    stem = str(name or "").strip().lower().split(".")[0]
    return any(stem == pre or stem.startswith(pre + "-") for pre in DUCK_HOSTNAME_PREFIXES)


#: Login accounts to try, in order.  ``radxa`` is the Armbian Radxa Zero 3 default.
CANDIDATE_USERS = ("duck", "radxa", "pi", "ubuntu", "armbian")

_SSH_OPTS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=4",
)


@dataclass
class DuckCandidate:
    """A host that might be a Microduck, and how much we have proven about it."""

    host: str
    source: str = "manual"
    transport: str = "ssh"
    user: Optional[str] = None
    ssh_open: bool = False
    ssh_auth: bool = False
    is_duck: bool = False
    in_robot_group: Optional[bool] = None
    robot_name: Optional[str] = None
    health: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """True when this duck can be driven right now, with no further setup."""
        if self.transport == "unix":
            return self.is_duck
        return self.is_duck and self.ssh_auth and self.in_robot_group is not False

    @property
    def blocker(self) -> Optional[str]:
        """The one thing standing between here and a driving duck, if any."""
        if not self.is_duck:
            return "robotd socket not found"
        if self.transport == "unix":
            return None
        if not self.ssh_auth:
            return "ssh key not installed"
        if self.in_robot_group is False:
            return "user not in 'robot' group"
        return None

    def describe(self) -> str:
        label = f"{self.user}@{self.host}" if self.user else self.host
        return f"{label} (via {self.source})"


# ---------------------------------------------------------------------------
# Shell helpers — every subprocess call in this module funnels through _run so
# tests can monkeypatch one function instead of the whole subprocess module.
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run *cmd*, returning ``(returncode, stdout, stderr)``. Never raises."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def ssh(
    host: str, user: Optional[str], command: str, timeout: float = 10.0
) -> tuple[int, str, str]:
    """Run *command* on the duck over SSH with key auth only (never prompts)."""
    dest = f"{user}@{host}" if user else host
    return _run(["ssh", *_SSH_OPTS, dest, command], timeout=timeout)


def _port_open(host: str, port: int = 22, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def local_socket_present(path: str = DEFAULT_SOCKET) -> bool:
    """True when robotd's socket is on *this* machine — OpenCastor runs on the duck."""
    try:
        import stat

        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def duckctl_ip(timeout: float = 8.0) -> Optional[str]:
    """Ask ``duckctl`` for the duck's address over Bluetooth, if it is installed.

    This is the most reliable path on the stock image — it does not depend on
    mDNS or on knowing the robot's hostname.
    """
    if shutil.which("duckctl") is None:
        return None
    rc, out, _ = _run(["duckctl", "ip"], timeout=timeout)
    if rc != 0 or not out:
        return None
    for token in out.replace(",", " ").split():
        token = token.strip()
        if token.count(".") == 3 and all(p.isdigit() for p in token.split(".")):
            return token
    return None


def probe_hostnames(
    hostnames: tuple[str, ...] = CANDIDATE_HOSTNAMES, timeout: float = 2.0
) -> list[str]:
    """Return the well-known duck hostnames that resolve and answer on port 22.

    Probes run in daemon threads bounded by *timeout*: a stalled ``.local``
    lookup (systemd-resolved can sit on one for five seconds) cannot make
    ``castor scan`` or ``castor duck`` hang, and cannot delay interpreter exit.
    """
    import threading

    found: list[str] = []
    lock = threading.Lock()

    def _probe(host: str) -> None:
        if _port_open(host, 22, timeout):
            with lock:
                found.append(host)

    threads = [threading.Thread(target=_probe, args=(h,), daemon=True) for h in hostnames]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    with lock:
        return [h for h in hostnames if h in found]


def mdns_hosts(timeout: float = 2.0) -> list[str]:
    """Browse mDNS for anything duck-shaped. Returns [] when zeroconf is absent.

    A **stock duck publishes nothing here** — this finds a duck that someone has
    added a record to, which in practice means duck-studio's bridge installer
    (``bridge/install.sh``, an Avahi ``_robotd._tcp`` record).  Both service types
    are browsed for that reason.
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore[import]
    except ImportError:
        return []

    import threading

    found: list[str] = []
    lock = threading.Lock()

    class _Handler:
        def add_service(self, zc, type_, name):  # noqa: D102, ANN001
            stem = name.split(".")[0]
            if not (looks_like_duck_hostname(stem) or "duck" in name.lower()):
                return
            host = stem + ".local"
            with lock:
                if host not in found:
                    found.append(host)

        def update_service(self, zc, type_, name):  # noqa: D102, ANN001
            pass

        def remove_service(self, zc, type_, name):  # noqa: D102, ANN001
            pass

    zc = None
    try:
        zc = Zeroconf()
        handler = _Handler()
        # _robotd._tcp is duck-studio's bridge record; _ssh._tcp is whatever else is
        # advertising a login. Neither is published by a stock duck.
        ServiceBrowser(zc, ["_robotd._tcp.local.", "_ssh._tcp.local."], handler)
        threading.Event().wait(timeout)
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.debug("microduck mDNS browse failed: %s", exc)
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    return found


def arp_neighbours() -> list[str]:
    """Return IPv4 addresses from the kernel neighbour table (no active scanning)."""
    hosts: list[str] = []
    rc, out, _ = _run(["ip", "-4", "neigh", "show"], timeout=5.0)
    if rc != 0:
        rc, out, _ = _run(["arp", "-an"], timeout=5.0)
        if rc != 0:
            return hosts
    for line in out.splitlines():
        for token in line.replace("(", " ").replace(")", " ").split():
            if token.count(".") == 3 and all(p.isdigit() for p in token.split(".")):
                if token not in hosts and not token.endswith(".255"):
                    hosts.append(token)
                break
    return hosts


#: Discovery methods, cheapest first. ``castor scan`` uses only :data:`FAST_METHODS`
#: so a routine hardware scan never pays for a Bluetooth wait.
#:
#: **mDNS is not in this list, because a stock duck does not publish any.** There is no
#: Avahi service file and no zeroconf registration anywhere in Pollen's repo, and their
#: own scripts route around name resolution rather than rely on it
#: (``scripts/dev-push.sh:80``, ``scripts/provision-board.sh:8``).  Browsing for one is
#: still worth doing when the operator has asked us to look hard — a duck running
#: duck-studio's bridge *does* register ``_robotd._tcp`` — so it lives in
#: :data:`DEEP_METHODS` and runs under ``--deep``, where its two-second wait is paid for
#: on purpose.
ALL_METHODS = ("local", "hostname", "duckctl")
FAST_METHODS = ("local", "hostname")
DEEP_METHODS = ("mdns",)


def discover(
    timeout: float = 3.0,
    deep: bool = False,
    extra_hosts: tuple[str, ...] = (),
    socket_path: str = DEFAULT_SOCKET,
    methods: tuple[str, ...] = ALL_METHODS,
) -> list[DuckCandidate]:
    """Find candidate ducks, cheapest and most certain method first.

    Args:
        timeout: Per-method budget in seconds.
        deep: Also sweep the ARP neighbour table. Slower, finds ducks with
              unknown hostnames and no Bluetooth in range.
        extra_hosts: Hosts supplied by the user, always probed first.
        socket_path: robotd socket path to test for a local duck.
        methods: Which discovery methods to run — see :data:`ALL_METHODS`.

    Returns:
        Candidates ordered best-first. Nothing is verified yet — call
        :func:`verify` to prove a candidate is really a duck.
    """
    candidates: list[DuckCandidate] = []
    seen: set[str] = set()

    def _add(host: str, source: str, transport: str = "ssh") -> None:
        key = host.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(DuckCandidate(host=host, source=source, transport=transport))

    for host in extra_hosts:
        _add(host, "manual")

    if "local" in methods and local_socket_present(socket_path):
        _add("localhost", "local", transport="unix")

    if "hostname" in methods:
        for host in probe_hostnames(timeout=min(timeout, 2.0)):
            _add(host, "hostname")

    if "duckctl" in methods:
        ip = duckctl_ip(timeout=timeout * 2)
        if ip:
            _add(ip, "duckctl")

    if "mdns" in methods or (deep and "mdns" in DEEP_METHODS):
        for host in mdns_hosts(timeout=min(timeout, 2.0)):
            _add(host, "mdns")

    if deep:
        neighbours = arp_neighbours()
        if neighbours:
            with ThreadPoolExecutor(max_workers=min(64, len(neighbours))) as pool:
                reachable = pool.map(lambda h: (h, _port_open(h, 22, 1.0)), neighbours)
            for host, ok in reachable:
                if ok:
                    _add(host, "arp")

    return candidates


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def resolve_ssh_user(
    host: str,
    users: tuple[str, ...] = CANDIDATE_USERS,
    timeout: float = 6.0,
) -> Optional[str]:
    """Return the first account on *host* that accepts our SSH key, or None.

    ``$USER`` is tried first — on a self-provisioned board the operator usually
    reused their own login.
    """
    ordered: list[str] = []
    me = os.environ.get("USER") or os.environ.get("USERNAME")
    if me:
        ordered.append(me)
    ordered.extend(u for u in users if u != me)

    for user in ordered:
        rc, _, _ = ssh(host, user, "true", timeout=timeout)
        if rc == 0:
            return user
    return None


def check_robot_group(host: str, user: str, timeout: float = 6.0) -> Optional[bool]:
    """Whether *user* is in the ``robot`` group (and so may open robotd's socket).

    Returns None when the check itself could not run.
    """
    rc, out, _ = ssh(host, user, "id -nG", timeout=timeout)
    if rc != 0:
        return None
    return "robot" in out.split()


def verify(candidate: DuckCandidate, timeout: float = 6.0) -> DuckCandidate:
    """Prove (or disprove) that *candidate* is a reachable Microduck.

    Mutates and returns the candidate with ``ssh_open``, ``ssh_auth``,
    ``is_duck``, ``in_robot_group`` and ``robot_name`` filled in.
    """
    if candidate.transport == "unix":
        candidate.is_duck = local_socket_present()
        return candidate

    candidate.ssh_open = _port_open(candidate.host, 22, timeout=min(timeout, 2.0))
    if not candidate.ssh_open:
        return candidate

    if not candidate.user:
        candidate.user = resolve_ssh_user(candidate.host, timeout=timeout)
    if not candidate.user:
        return candidate

    rc, _, _ = ssh(candidate.host, candidate.user, "true", timeout=timeout)
    candidate.ssh_auth = rc == 0
    if not candidate.ssh_auth:
        return candidate

    # Decisive test: robotd's socket exists on the far end.
    rc, out, _ = ssh(
        candidate.host, candidate.user, f"test -S {DEFAULT_SOCKET} && echo duck", timeout=timeout
    )
    candidate.is_duck = rc == 0 and "duck" in out

    if candidate.is_duck:
        candidate.in_robot_group = check_robot_group(candidate.host, candidate.user, timeout)
        rc, out, _ = ssh(
            candidate.host, candidate.user, "robotctl system info 2>/dev/null", timeout=timeout
        )
        if rc == 0 and out:
            candidate.robot_name = _parse_robot_name(out)

    return candidate


def _parse_robot_name(text: str) -> Optional[str]:
    """Pull the robot name out of ``robotctl system info`` output."""
    for line in text.splitlines():
        low = line.lower()
        if "name" in low and (":" in line or "=" in line):
            sep = ":" if ":" in line else "="
            value = line.split(sep, 1)[1].strip().strip("\"'")
            if value:
                return value
    return None


def ssh_copy_id_command(host: str, user: Optional[str]) -> str:
    """The exact command that fixes 'ssh key not installed'."""
    dest = f"{user}@{host}" if user else host
    return f"ssh-copy-id {dest}"


def robot_group_command(host: str, user: Optional[str]) -> str:
    """The exact command that fixes 'user not in robot group'."""
    dest = f"{user}@{host}" if user else host
    return f"ssh {dest} 'sudo usermod -aG robot $USER' && ssh {dest} sudo reboot"


# ---------------------------------------------------------------------------
# Live health
# ---------------------------------------------------------------------------


def health(
    host: Optional[str] = None,
    user: Optional[str] = None,
    transport: str = "ssh",
    timeout: float = 8.0,
    **driver_kwargs: Any,
) -> dict:
    """Open the real driver, call ``robot.health``, close. Returns the health dict.

    Adds ``"ok": False`` with an ``"error"`` rather than raising, so callers can
    print a result either way.
    """
    from castor.drivers.microduck_driver import MicroduckDriver

    cfg: dict[str, Any] = {"transport": transport, "rpc_timeout_s": timeout}
    if transport == "ssh":
        cfg.update({"ssh_host": host, "ssh_user": user})
    elif transport == "tcp":
        cfg.update({"host": host})
    cfg.update(driver_kwargs)

    driver = None
    try:
        driver = MicroduckDriver(cfg)
        if driver._mode != "hardware":
            return {"ok": False, "mode": "mock", "error": "could not reach robotd"}
        result = driver.health_check()
        result["policies"] = driver.get_policies()
        result["policy_slots"] = driver.get_policy_slots()
        result["envelope"] = driver.envelope
        return result
    except Exception as exc:
        return {"ok": False, "mode": "error", "error": str(exc)}
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def profile_path() -> Optional[Path]:
    """Locate the microduck profile — repo preset first, packaged profile second."""
    pkg_dir = Path(__file__).resolve().parent
    for path in (
        pkg_dir.parent / "config" / "presets" / f"{PRESET_ID}.rcan.yaml",
        pkg_dir / "profiles" / "pollen" / "microduck.yaml",
    ):
        if path.exists():
            return path
    return None


def load_profile() -> dict:
    """Load the microduck RCAN profile as a dict.

    Raises:
        FileNotFoundError: if neither the repo preset nor the packaged profile
            can be found (a broken install).
    """
    path = profile_path()
    if path is None:
        raise FileNotFoundError(
            "microduck profile not found — expected config/presets/"
            f"{PRESET_ID}.rcan.yaml or castor/profiles/pollen/microduck.yaml"
        )
    return yaml.safe_load(path.read_text()) or {}


def build_config(
    host: Optional[str] = None,
    user: Optional[str] = None,
    robot_name: str = "duck",
    transport: str = "ssh",
    agent: Optional[dict] = None,
    rrn: Optional[str] = None,
) -> dict:
    """Materialise the microduck profile for one specific duck.

    Args:
        host: The duck's address. Ignored for ``transport="unix"``.
        user: SSH login on the duck.
        robot_name: Name for this robot in OpenCastor and the Fleet UI.
        transport: ``"ssh"``, ``"unix"`` or ``"tcp"``.
        agent: Optional ``{"provider": …, "model": …}`` override.
        rrn: Optional pre-assigned RRN.

    Returns:
        A complete RCAN config dict, ready to write.
    """
    config = load_profile()
    config.pop("profile", None)
    config.pop("robot_type", None)

    meta = config.setdefault("metadata", {})
    meta["robot_name"] = robot_name
    meta["robot_uuid"] = str(uuid.uuid4())
    meta["created_at"] = datetime.now(timezone.utc).isoformat()
    meta["rrn_uri"] = f"rrn://community/robot/opencastor/pollen-microduck/{robot_name}"
    if rrn:
        meta["rrn"] = rrn

    if agent:
        config.setdefault("agent", {}).update(agent)

    drivers = config.get("drivers") or []
    for entry in drivers:
        if entry.get("protocol") != "microduck":
            continue
        entry["transport"] = transport
        if transport == "ssh":
            entry["ssh_host"] = host
            if user:
                entry["ssh_user"] = user
            else:
                entry.pop("ssh_user", None)
        elif transport == "unix":
            for key in ("ssh_host", "ssh_user", "local_port", "host", "port"):
                entry.pop(key, None)
        elif transport == "tcp":
            entry["host"] = host
            for key in ("ssh_host", "ssh_user"):
                entry.pop(key, None)

    conn = config.setdefault("connection", {})
    if transport == "unix":
        conn["type"] = "local"
        conn.pop("host", None)
    else:
        conn["type"] = "wifi"
        conn["host"] = host

    return config


#: The body of the generated ROBOT.md.  Frontmatter above it is the machine's half;
#: this is the half a human or an agent harness reads at session start.
_MANIFEST_BODY = """\
# {robot_name}

A Pollen Robotics Microduck, configured by `castor duck` on {created}.

It is a robot before OpenCastor arrives: its own compute, its own 50 Hz control
loop, its own trained policies and its own safety envelope. OpenCastor supplies
the brain and nothing else.

- Reach it: `{reach}`
- Make it walk, no brain needed: `castor duck test`
- Ask robotd how it is: `castor duck health`
- Run it under the brain: `castor run --config {manifest_path}`

## Velocity envelope

Full deflection maps to **{max_vx} m/s** forward, {max_vy} m/s lateral and
{max_vyaw} rad/s of yaw. Pollen's own gamepad daemon allows 0.3 m/s and
1.5 rad/s (`padd/src/main.rs:139-163`), so this is a deliberately smaller
envelope, not a robot that is struggling. `castor duck test --speed` walks it
anywhere inside the envelope.

## What is not verified here

{verification}
"""


def build_manifest(config: dict, manifest_path: str = "<manifest>", verified: bool = False) -> str:
    """Render *config* as a ROBOT.md — the manifest ``castor run`` accepts.

    ``castor duck`` used to write ``<name>.rcan.yaml`` and print
    ``castor run --config <that file>``, which ``cmd_run`` rejects on the suffix
    alone (``castor/cli.py:57-67``, ``:207``).  The two halves of the product
    disagreed about the config format and the half that generates was losing.
    This is that fix: one file, written by the setup command, accepted by the run
    command, carrying **the whole config including the ``drivers`` block** — without
    which ``ComponentRegistry.get_driver`` returns ``None``
    (``castor/registry.py:201-202``) and a configured duck becomes no robot.

    Args:
        config: The dict from :func:`build_config`.
        manifest_path: Where this manifest will live, for the body's own example.
        verified: Whether ``robot.health`` actually answered during setup.

    Returns:
        The complete ROBOT.md text, frontmatter and body.
    """
    fm = {k: v for k, v in config.items() if v is not None}
    fm.setdefault("rcan_version", "3.2")

    meta = fm.get("metadata") or {}
    robot_name = meta.get("robot_name", "duck")

    driver_cfg: dict = {}
    for entry in fm.get("drivers") or []:
        if entry.get("protocol") == "microduck":
            driver_cfg = entry
            break

    transport = driver_cfg.get("transport", "unix")
    if transport == "ssh":
        host = driver_cfg.get("ssh_host", "<host>")
        user = driver_cfg.get("ssh_user")
        reach = f"ssh {user}@{host}" if user else f"ssh {host}"
    elif transport == "tcp":
        reach = f"{driver_cfg.get('host', '<host>')}:{driver_cfg.get('port', 7788)}"
    else:
        reach = driver_cfg.get("socket", "/run/robotd.sock") + " (this machine is the duck)"

    verification = (
        "`robot.health` answered during setup: the loop rate, battery and policy\n"
        "slots recorded above were read off the wire, not assumed."
        if verified
        else "**`robot.health` did not answer during setup.** Nothing below the\n"
        "transport has been proven about this robot. Run `castor duck health`\n"
        "before trusting any of it."
    )

    body = _MANIFEST_BODY.format(
        robot_name=robot_name,
        created=meta.get("created_at", "an unrecorded date"),
        reach=reach,
        manifest_path=manifest_path,
        max_vx=driver_cfg.get("max_vx", 0.2),
        max_vy=driver_cfg.get("max_vy", 0.1),
        max_vyaw=driver_cfg.get("max_vyaw", 1.0),
        verification=verification,
    )
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=95)
    return f"---\n{serialized}---\n\n{body}"


def manifest_path_for(robot_name: str = "duck") -> Path:
    """Where :func:`write_manifest` puts a duck's ROBOT.md by default."""
    return config_dir() / f"{robot_name}.ROBOT.md"


def write_manifest(
    config: dict,
    robot_name: str = "duck",
    path: Optional[Path] = None,
    verified: bool = False,
) -> Path:
    """Write *config* as ``~/.config/opencastor/<robot_name>.ROBOT.md``.

    Returns:
        The path written — the exact path ``castor run --config`` should be given.
    """
    target = Path(path) if path else manifest_path_for(robot_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_manifest(config, manifest_path=str(target), verified=verified))
    return target


def config_dir() -> Path:
    """Return ``~/.config/opencastor``, creating it if needed."""
    path = Path.home() / ".config" / "opencastor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_config(config: dict, robot_name: str = "duck", path: Optional[Path] = None) -> Path:
    """Write *config* to ``~/.config/opencastor/<robot_name>.rcan.yaml``.

    Args:
        config: RCAN config dict from :func:`build_config`.
        robot_name: Used for the default filename.
        path: Explicit destination, overriding the default.

    Returns:
        The path written.
    """
    target = Path(path) if path else config_dir() / f"{robot_name}.rcan.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.dump(config, sort_keys=False, default_flow_style=False, width=95))
    return target
