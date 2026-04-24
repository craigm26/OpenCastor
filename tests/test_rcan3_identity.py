"""Tests for castor.rcan3.identity — PQ keypair management."""

from __future__ import annotations

import pytest


def test_load_or_generate_creates_new_keypair(tmp_path):
    from castor.rcan3.identity import CastorIdentity, load_or_generate_identity

    ident = load_or_generate_identity(keydir=tmp_path)
    assert isinstance(ident, CastorIdentity)
    assert ident.public_key_jwk is not None
    assert (tmp_path / "ml_dsa.pub.jwk").exists()
    assert (tmp_path / "ml_dsa.priv.jwk").exists()


def test_load_or_generate_is_idempotent(tmp_path):
    """Second call returns the same keypair — does not regenerate."""
    from castor.rcan3.identity import load_or_generate_identity

    first = load_or_generate_identity(keydir=tmp_path)
    second = load_or_generate_identity(keydir=tmp_path)
    assert first.public_key_jwk == second.public_key_jwk


def test_keys_are_chmod_600(tmp_path):
    """Private key file has mode 0o600 (owner rw only)."""
    from castor.rcan3.identity import load_or_generate_identity

    load_or_generate_identity(keydir=tmp_path)
    priv = tmp_path / "ml_dsa.priv.jwk"
    assert oct(priv.stat().st_mode)[-3:] == "600"


def test_jwk_format_has_kty_and_alg(tmp_path):
    from castor.rcan3.identity import load_or_generate_identity

    ident = load_or_generate_identity(keydir=tmp_path)
    pub = ident.public_key_jwk
    assert "kty" in pub
    assert pub.get("alg") is not None and "dsa" in pub.get("alg", "").lower()


def test_explicit_keydir_override(tmp_path):
    """Passing an explicit keydir overrides the default ~/.castor/keys/."""
    from castor.rcan3.identity import load_or_generate_identity

    load_or_generate_identity(keydir=tmp_path / "custom")
    assert (tmp_path / "custom" / "ml_dsa.pub.jwk").exists()


def test_corrupt_privkey_raises(tmp_path):
    """If the private key file exists but is corrupt, raise rather than
    silently regenerate (avoid destroying a real robot's identity)."""
    from castor.rcan3.identity import load_or_generate_identity

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "ml_dsa.pub.jwk").write_text('{"kty": "bogus"}')
    (tmp_path / "ml_dsa.priv.jwk").write_text("not-valid-jwk")
    with pytest.raises(ValueError, match="corrupt"):
        load_or_generate_identity(keydir=tmp_path)
