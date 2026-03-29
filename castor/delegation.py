"""castor/delegation.py — RCAN delegation chain management (§delegation)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

MAX_DELEGATION_DEPTH = 3


@dataclass
class DelegationHop:
    robot_rrn: str
    scope: str
    issued_at: str
    expires_at: str
    sig: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate_chain(chain: list[Any]) -> None:
    if len(chain) > MAX_DELEGATION_DEPTH:
        raise ValueError(
            f"RCAN: delegation chain max depth is {MAX_DELEGATION_DEPTH}, got {len(chain)}"
        )


def build_hop(robot_rrn: str, scope: str, ttl_seconds: int = 3600) -> dict:
    now = time.time()
    return {
        "robot_rrn": robot_rrn,
        "scope": scope,
        "issued_at": str(int(now)),
        "expires_at": str(int(now + ttl_seconds)),
        "sig": "",  # Populated by signing layer
    }


def verify_chain(chain: list[Any], expected_rrn: str = "") -> bool:
    """Verify delegation chain structure. Signature verification is a stub."""
    try:
        validate_chain(chain)
    except ValueError:
        return False
    # TODO: verify cryptographic signatures when key-rotation infrastructure is complete
    import logging

    logging.getLogger(__name__).warning(
        "RCAN: delegation chain signature verification is a stub — update in v2026.4.x"
    )
    return True
