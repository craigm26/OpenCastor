"""Tests for castor.crypto.pqc — pqc-hybrid-v1 robot identity (issue #808)."""

from __future__ import annotations

import json
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path

import pytest

from castor.crypto.pqc import (
    RobotKeyPair,
    generate_robot_keypair,
    load_or_generate_robot_keypair,
    load_robot_keypair,
    robot_identity_record,
    save_robot_keypair,
    sign_robot_message,
    verify_robot_message,
)


# ---------------------------------------------------------------------------
# generate_robot_keypair
# ---------------------------------------------------------------------------


def test_generate_returns_robot_key_pair():
    kp = generate_robot_keypair()
    assert isinstance(kp, RobotKeyPair)


def test_generate_ed25519_key_sizes():
    kp = generate_robot_keypair()
    assert len(kp.ed25519_private) == 32
    assert len(kp.ed25519_public) == 32


def test_generate_ml_dsa_key_sizes():
    kp = generate_robot_keypair()
    # ML-DSA-65: public key 1952 B, secret key 4032 B
    assert len(kp.ml_dsa_public) == 1952
    assert len(kp.ml_dsa_private) == 4032


def test_generate_profile():
    kp = generate_robot_keypair()
    assert kp.profile == "pqc-hybrid-v1"


def test_generate_produces_distinct_keypairs():
    kp1 = generate_robot_keypair()
    kp2 = generate_robot_keypair()
    assert kp1.ed25519_public != kp2.ed25519_public
    assert kp1.ml_dsa_public != kp2.ml_dsa_public


# ---------------------------------------------------------------------------
# sign_robot_message / verify_robot_message
# ---------------------------------------------------------------------------


def test_sign_returns_string():
    kp = generate_robot_keypair()
    sig = sign_robot_message(kp, b"hello")
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_sign_and_verify_roundtrip():
    kp = generate_robot_keypair()
    message = b"robot:bob:command:move_forward"
    sig = sign_robot_message(kp, message)
    assert verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, message, sig)


def test_verify_rejects_wrong_message():
    kp = generate_robot_keypair()
    sig = sign_robot_message(kp, b"original message")
    assert not verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, b"tampered message", sig)


def test_verify_rejects_wrong_ed25519_key():
    kp1 = generate_robot_keypair()
    kp2 = generate_robot_keypair()
    sig = sign_robot_message(kp1, b"hello")
    # Different Ed25519 public key — must fail
    assert not verify_robot_message(kp2.ed25519_public, kp1.ml_dsa_public, b"hello", sig)


def test_verify_rejects_wrong_ml_dsa_key():
    kp1 = generate_robot_keypair()
    kp2 = generate_robot_keypair()
    sig = sign_robot_message(kp1, b"hello")
    # Different ML-DSA public key — must fail
    assert not verify_robot_message(kp1.ed25519_public, kp2.ml_dsa_public, b"hello", sig)


def test_verify_rejects_corrupted_signature():
    kp = generate_robot_keypair()
    sig = sign_robot_message(kp, b"hello")
    # Corrupt the base64 payload
    corrupted = sig[:-4] + "XXXX"
    assert not verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, b"hello", corrupted)


def test_verify_rejects_empty_signature():
    kp = generate_robot_keypair()
    assert not verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, b"hello", "")


def test_verify_rejects_garbled_signature():
    kp = generate_robot_keypair()
    assert not verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, b"hello", "notbase64!!!")


def test_sign_envelope_structure():
    kp = generate_robot_keypair()
    sig = sign_robot_message(kp, b"test")
    padded = sig + "=" * (-len(sig) % 4)
    envelope = json.loads(urlsafe_b64decode(padded))
    assert envelope["profile"] == "pqc-hybrid-v1"
    assert "ed25519" in envelope
    assert "ml_dsa_65" in envelope


def test_sign_is_deterministic_in_structure_not_value():
    # Two signatures for same message should both verify (ML-DSA is randomized)
    kp = generate_robot_keypair()
    msg = b"same message"
    sig1 = sign_robot_message(kp, msg)
    sig2 = sign_robot_message(kp, msg)
    assert verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, msg, sig1)
    assert verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, msg, sig2)


# ---------------------------------------------------------------------------
# robot_identity_record
# ---------------------------------------------------------------------------


def test_identity_record_has_required_fields():
    kp = generate_robot_keypair()
    record = robot_identity_record(kp)
    assert record["crypto_profile"] == "pqc-hybrid-v1"
    assert "pqc_public_key" in record
    assert "ed25519_public_key" in record


def test_identity_record_no_private_key_material():
    kp = generate_robot_keypair()
    record = robot_identity_record(kp)
    # Private keys must not appear in the record
    record_str = json.dumps(record)
    ed_priv_b64 = urlsafe_b64encode(kp.ed25519_private).decode()
    ml_priv_b64 = urlsafe_b64encode(kp.ml_dsa_private).decode()
    assert ed_priv_b64 not in record_str
    assert ml_priv_b64 not in record_str


def test_identity_record_public_keys_are_base64url():
    kp = generate_robot_keypair()
    record = robot_identity_record(kp)
    # Should be valid base64url (urlsafe_b64decode must not raise)
    pqc_key = record["pqc_public_key"]
    ed_key = record["ed25519_public_key"]
    decoded_pqc = urlsafe_b64decode(pqc_key + "=" * (-len(pqc_key) % 4))
    decoded_ed = urlsafe_b64decode(ed_key + "=" * (-len(ed_key) % 4))
    assert decoded_pqc == kp.ml_dsa_public
    assert decoded_ed == kp.ed25519_public


# ---------------------------------------------------------------------------
# Keypair persistence
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    kp = generate_robot_keypair()
    path = tmp_path / "robot_identity.json"
    save_robot_keypair(kp, path)
    kp2 = load_robot_keypair(path)
    assert kp.ed25519_private == kp2.ed25519_private
    assert kp.ed25519_public == kp2.ed25519_public
    assert kp.ml_dsa_private == kp2.ml_dsa_private
    assert kp.ml_dsa_public == kp2.ml_dsa_public
    assert kp.profile == kp2.profile


def test_load_or_generate_creates_file(tmp_path):
    path = tmp_path / "robot_identity.json"
    assert not path.exists()
    kp, generated = load_or_generate_robot_keypair(path)
    assert generated is True
    assert path.exists()


def test_load_or_generate_loads_existing(tmp_path):
    path = tmp_path / "robot_identity.json"
    kp1 = generate_robot_keypair()
    save_robot_keypair(kp1, path)
    kp2, generated = load_or_generate_robot_keypair(path)
    assert generated is False
    assert kp1.ed25519_public == kp2.ed25519_public


def test_load_or_generate_keys_verify_after_reload(tmp_path):
    path = tmp_path / "robot_identity.json"
    kp, _ = load_or_generate_robot_keypair(path)
    msg = b"fleet:command:expand"
    sig = sign_robot_message(kp, msg)

    # Reload from disk and verify
    kp2, _ = load_or_generate_robot_keypair(path)
    assert verify_robot_message(kp2.ed25519_public, kp2.ml_dsa_public, msg, sig)
