import logging
import os
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("OpenCastor.RobotContext")

_MEMORY_PATH = os.path.expanduser("~/.opencastor/robot-memory.md")
_LOG_PATH = "/tmp/castor-gateway.log"
_MEMORY_TRUNCATE = 2000


@dataclass
class RobotContext:
    rrn: str = ""
    hostname: str = ""
    crypto_profile: str = ""
    firmware_version: str = ""
    uptime: str = ""
    hardware: dict = field(default_factory=dict)
    active_loa: int = 0
    last_errors: list[str] = field(default_factory=list)
    last_commands: list[str] = field(default_factory=list)
    session_memory: str = ""
    generated_at: str = ""


def build_robot_context(config: dict) -> RobotContext:
    """Build a RobotContext snapshot from the active RCAN config dict."""
    metadata = config.get("metadata", {})
    rrn = metadata.get("rrn", "") or metadata.get("rrn_uri", "")
    crypto_profile = config.get("pqc", {}).get("profile", "")
    firmware_version = metadata.get("version", "")

    # Hardware subsystems from drivers list
    hardware: dict[str, str] = {}
    for drv in config.get("drivers", []):
        if isinstance(drv, dict):
            drv_id = drv.get("id") or drv.get("protocol", "unknown")
            hardware[drv_id] = drv.get("status", "configured")

    # Active LoA from safety config
    active_loa = int(config.get("safety", {}).get("loa", 0))

    # Hostname
    hostname = socket.gethostname()

    # Uptime — read /proc/uptime (reliable on Linux) or fall back to `uptime -p`
    uptime = ""
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        uptime = f"{h}h {m}m"
    except Exception:
        try:
            uptime = subprocess.check_output(["uptime", "-p"], text=True).strip()
        except Exception:
            uptime = ""

    # Last errors from gateway log (last 5 ERROR|WARN lines)
    last_errors: list[str] = []
    try:
        with open(_LOG_PATH) as f:
            lines = f.readlines()
        for line in reversed(lines):
            if "ERROR" in line or "WARN" in line:
                last_errors.append(line.rstrip())
                if len(last_errors) >= 5:
                    break
        last_errors.reverse()
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Could not read gateway log: %s", exc)

    # Session memory — truncated to 2000 chars
    session_memory = ""
    try:
        with open(_MEMORY_PATH) as f:
            session_memory = f.read()[:_MEMORY_TRUNCATE]
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Could not read robot memory: %s", exc)

    return RobotContext(
        rrn=rrn,
        hostname=hostname,
        crypto_profile=crypto_profile,
        firmware_version=firmware_version,
        uptime=uptime,
        hardware=hardware,
        active_loa=active_loa,
        last_errors=last_errors,
        last_commands=[],
        session_memory=session_memory,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def format_robot_context(ctx: RobotContext) -> str:
    """Render a RobotContext as an XML-style block for injection into prompts.

    Returns a ``<robot-context>`` block.  Empty fields are omitted.
    No cache_control markers — this is dynamic content.
    """
    lines: list[str] = []

    if ctx.rrn:
        lines.append(f"  <rrn>{ctx.rrn}</rrn>")
    if ctx.hostname:
        lines.append(f"  <hostname>{ctx.hostname}</hostname>")
    if ctx.crypto_profile:
        lines.append(f"  <crypto_profile>{ctx.crypto_profile}</crypto_profile>")
    if ctx.firmware_version:
        lines.append(f"  <firmware_version>{ctx.firmware_version}</firmware_version>")
    if ctx.uptime:
        lines.append(f"  <uptime>{ctx.uptime}</uptime>")
    if ctx.hardware:
        hw_items = ", ".join(f"{k}={v}" for k, v in ctx.hardware.items())
        lines.append(f"  <hardware>{hw_items}</hardware>")
    if ctx.active_loa:
        lines.append(f"  <active_loa>{ctx.active_loa}</active_loa>")
    if ctx.last_errors:
        errors_block = "\n".join(f"    {e}" for e in ctx.last_errors)
        lines.append(f"  <last_errors>\n{errors_block}\n  </last_errors>")
    if ctx.last_commands:
        cmds_block = "\n".join(f"    {c}" for c in ctx.last_commands)
        lines.append(f"  <last_commands>\n{cmds_block}\n  </last_commands>")
    if ctx.session_memory:
        lines.append(f"  <session_memory>{ctx.session_memory}</session_memory>")

    inner = "\n".join(lines)
    return f'<robot-context generated="{ctx.generated_at}">\n{inner}\n</robot-context>'
