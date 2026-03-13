"""
castor/quantum_commitment.py — OpenCastor ↔ QuantumLink-Sim integration.

Provides a thin adapter that wires CommitmentEngine into OpenCastor's audit
pipeline, with graceful degradation when quantumlink_sim is not installed.

RCAN config keys (under ``security.commitment``)::

    security:
      commitment:
        enabled: true
        mode: hybrid          # classical | quantum | hybrid
        pool_size: 32         # pre-generated QKD keys in memory
        n_qkd_bits: 512       # raw BB84 qubits per run
        qber_threshold: 0.11  # max acceptable QBER
        use_qiskit: false     # use Qiskit circuit backend (Pi: keep false)
        storage_path: .opencastor-commitments.jsonl
        export_secret_path: .opencastor-chain-secret.hex

Usage (from castor/audit.py or castor/main.py)::

    from castor.quantum_commitment import build_commitment_engine

    engine = build_commitment_engine(config)
    if engine:
        engine.start()
        ...
        record = engine.commit(audit_entry_dict)
        ...
        engine.stop()

CLI::

    castor commit verify         # Verify chain integrity
    castor commit stats          # Pool + chain statistics
    castor commit export         # Export JSONL chain
    castor commit proof <id>     # Print proof bundle for a record
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.QuantumCommitment")

# ---------------------------------------------------------------------------
# Optional import — graceful degradation
# ---------------------------------------------------------------------------

try:
    from quantumlink_sim.commitment import CommitmentEngine, KeyMode

    _QUANTUMLINK_AVAILABLE = True
except ImportError:
    _QUANTUMLINK_AVAILABLE = False
    CommitmentEngine = None  # type: ignore[assignment,misc]
    KeyMode = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_commitment_engine(config: dict[str, Any]) -> Optional[CommitmentEngine]:
    """Build a CommitmentEngine from the RCAN config dict.

    Returns ``None`` if:
    - ``security.commitment.enabled`` is falsy, or
    - ``quantumlink_sim`` is not installed.

    Args:
        config: Full RCAN config dict (as loaded by ``castor.configure``).

    Returns:
        A started CommitmentEngine, or None.
    """
    if not _QUANTUMLINK_AVAILABLE:
        logger.info(
            "quantumlink_sim not installed — quantum commitment disabled. "
            "Install with: pip install quantumlink-sim"
        )
        return None

    sec = config.get("security", {})
    cfg = sec.get("commitment", {})

    if not cfg.get("enabled", False):
        return None

    mode_str = cfg.get("mode", "hybrid").lower()
    try:
        mode = KeyMode(mode_str)
    except ValueError:
        logger.warning("Unknown commitment mode %r, defaulting to hybrid", mode_str)
        mode = KeyMode("hybrid")

    # Load chain secret from file if previously exported (enables cross-session verify)
    chain_secret: Optional[bytes] = None
    secret_path = cfg.get("export_secret_path", ".opencastor-chain-secret.hex")
    if os.path.exists(secret_path):
        try:
            chain_secret = bytes.fromhex(open(secret_path).read().strip())
            logger.info("Loaded chain secret from %s", secret_path)
        except Exception as exc:
            logger.warning("Could not load chain secret: %s — generating new one", exc)

    engine = CommitmentEngine(
        mode=mode,
        pool_size=cfg.get("pool_size", 32),
        n_qkd_bits=cfg.get("n_qkd_bits", 512),
        qber_threshold=cfg.get("qber_threshold", 0.11),
        use_qiskit=cfg.get("use_qiskit", False),
        storage_path=cfg.get("storage_path", ".opencastor-commitments.jsonl"),
        chain_secret=chain_secret,
    )
    engine.start()

    # Persist the chain secret so verification is possible across restarts
    if secret_path and not os.path.exists(secret_path):
        try:
            with open(secret_path, "w") as f:
                f.write(engine.export_chain_secret())
            logger.info("Chain secret saved to %s (keep this file secure)", secret_path)
        except Exception as exc:
            logger.warning("Could not save chain secret: %s", exc)

    logger.info(
        "CommitmentEngine started (mode=%s, pool=%d, qiskit=%s)",
        mode.value,
        cfg.get("pool_size", 32),
        cfg.get("use_qiskit", False),
    )
    return engine


# ---------------------------------------------------------------------------
# CLI helpers (called from castor/cli.py)
# ---------------------------------------------------------------------------


def cli_verify(engine: Optional[CommitmentEngine]) -> None:
    """Print chain verification result."""
    if engine is None:
        print("Quantum commitment not enabled.")
        return
    ok, broken = engine.verify_chain()
    if ok:
        stats = engine.stats()
        print(
            f"✅ Chain intact — {stats['chain_length']} records verified.\n"
            f"   Head: {stats['chain_head'][:16]}..."
        )
    else:
        print(f"❌ Chain broken at record index {broken}. Possible tampering.")


def cli_stats(engine: Optional[CommitmentEngine]) -> None:
    """Print engine statistics."""
    if engine is None:
        print("Quantum commitment not enabled.")
        return
    import json

    print(json.dumps(engine.stats(), indent=2))


def cli_export(engine: Optional[CommitmentEngine]) -> None:
    """Print JSONL export of all records."""
    if engine is None:
        print("Quantum commitment not enabled.")
        return
    print(engine.export_jsonl())


def cli_proof(engine: Optional[CommitmentEngine], record_id: str) -> None:
    """Print proof bundle for a specific record."""
    if engine is None:
        print("Quantum commitment not enabled.")
        return
    import json

    proof = engine.export_proof(record_id)
    if proof:
        print(json.dumps(proof, indent=2))
    else:
        print(f"Record {record_id!r} not found in current chain.")
