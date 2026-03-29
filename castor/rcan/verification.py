"""
RCAN robot verification tier management for OpenCastor.
Mirrors the rcan.dev verification tier system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VerificationTier(str, Enum):
    COMMUNITY = "community"
    VERIFIED = "verified"
    CERTIFIED = "certified"
    ACCREDITED = "accredited"


TIER_ORDER = [
    VerificationTier.COMMUNITY,
    VerificationTier.VERIFIED,
    VerificationTier.CERTIFIED,
    VerificationTier.ACCREDITED,
]

TIER_BADGES = {
    VerificationTier.COMMUNITY: "⬜",
    VerificationTier.VERIFIED: "🟡",
    VerificationTier.CERTIFIED: "🔵",
    VerificationTier.ACCREDITED: "✅",
}


@dataclass
class VerificationStatus:
    rrn: str
    tier: VerificationTier
    evidence_url: Optional[str] = None
    verified_at: Optional[str] = None

    @property
    def badge(self) -> str:
        return TIER_BADGES[self.tier]

    @property
    def display(self) -> str:
        return f"{self.badge} {self.tier.value.title()}"


def get_tier_from_rrn(rrn: str) -> Optional[VerificationStatus]:
    """
    Look up verification tier for an RRN via rcan.dev API.
    Returns None if unavailable (network down or RRN not found).
    """
    try:
        import json
        import urllib.request

        url = f"https://robotregistryfoundation.org/v2/robots/{rrn}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            tier = data.get("verification_tier", "community")
            return VerificationStatus(
                rrn=rrn,
                tier=VerificationTier(tier),
                evidence_url=data.get("evidence_url"),
                verified_at=data.get("verified_at"),
            )
    except Exception:
        return None


def can_upgrade_to(current: VerificationTier, target: VerificationTier) -> bool:
    """Check if upgrade is valid (only one step at a time)."""
    current_idx = TIER_ORDER.index(current)
    target_idx = TIER_ORDER.index(target)
    return target_idx == current_idx + 1
