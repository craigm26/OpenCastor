"""RCAN integration for SO-ARM101 — publish arm poses via RCAN COMMAND messages.

Provides ``send_arm_pose_rcan`` which builds a MessageType 1 (COMMAND) with
``action="arm_pose"`` and logs it for the audit trail.  When a RCAN transport
client becomes available, the ``# TODO`` stub below is the insertion point.

All errors degrade gracefully — the function always returns ``True`` (message
built) or ``False`` (could not build/log), and never raises.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Hardware.SOArm101.RCANBridge")


def send_arm_pose_rcan(
    joint_positions: dict,
    rcan_config_path: str = "~/opencastor/bob.rcan.yaml",
    ruri: Optional[str] = None,
) -> bool:
    """Publish arm joint positions as a RCAN COMMAND message.

    Builds a MessageType 1 (COMMAND) with ``action="arm_pose"`` and the joint
    positions as payload.  Falls back gracefully if RCAN is not configured.

    The RCAN config YAML is read to obtain the robot's RURI.  If the file does
    not exist, a default RURI is used.  In both cases the message is logged at
    INFO level to provide a full audit trail even before a transport client is
    wired in.

    Args:
        joint_positions: Dict mapping joint names to target positions (radians).
        rcan_config_path: Path to rcan.yaml for identity/RURI.
        ruri: Override RURI (if not read from config).

    Returns:
        ``True`` if message was built and logged; ``False`` if RCAN not available.
    """
    try:
        import yaml  # type: ignore[import]

        config_path = Path(rcan_config_path).expanduser()
        if ruri is None and config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            ruri = cfg.get("ruri", "rcan://local/unknown/arm/so-arm101")

        msg = {
            "message_type": 1,  # COMMAND
            "ruri": ruri or "rcan://local/unknown/arm/so-arm101",
            "action": "arm_pose",
            "payload": {
                "joint_positions": joint_positions,
                "timestamp_ms": int(time.time() * 1000),
            },
            "message_id": str(uuid.uuid4()),
            "timestamp_ms": int(time.time() * 1000),
        }
        logger.info("RCAN arm_pose: %s", json.dumps(msg, indent=2))
        # Send via RCAN HTTP transport
        try:
            from castor.rcan.http_transport import send_message

            cfg = None
            config_path = Path(rcan_config_path).expanduser()
            if config_path.exists():
                import yaml as _yaml  # type: ignore[import]

                with open(config_path) as _f:
                    cfg = _yaml.safe_load(_f)
            target_host = (
                cfg.get("rcan_protocol", {}).get("peers", [{}])[0].get("host") if cfg else None
            )
            if target_host:
                send_message(target_host, msg)
                logger.info("RCAN arm_pose sent to %s", target_host)
            else:
                logger.debug("No RCAN peers configured — message logged only")
        except Exception as _e:
            logger.debug("RCAN transport send failed (non-fatal): %s", _e)
        return True
    except Exception as exc:
        logger.debug("RCAN pose send failed (non-fatal): %s", exc)
        return False
