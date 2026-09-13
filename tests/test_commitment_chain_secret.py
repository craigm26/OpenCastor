"""The commitment chain ships no key, so it is off until one is provisioned.

Until 3.5 ``_resolve_secret`` ended by returning a literal secret compiled into
the package. Because a non-empty bytestring is truthy, ``enabled`` was True on
every install that had never been given a key, and the runtime sealed a record
for every action under a key anyone holding the wheel also held. These tests
pin the fail-closed replacement.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from castor.rcan.commitment_chain import CommitmentChain

_KEY_ENVS = ("OPENCASTOR_COMMITMENT_SECRET", "OPENCASTOR_COMMITMENT_SECRET_FILE")


@pytest.fixture
def no_key_env(monkeypatch):
    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_chain_disabled_without_secret(tmp_path, no_key_env):
    """No key anywhere: disabled, append is a no-op, nothing on disk."""
    log_path = tmp_path / "commitments.jsonl"
    chain = CommitmentChain(log_path=log_path)

    assert chain._secret is None
    assert chain.enabled is False
    assert chain.append_action("move_forward", {"distance_m": 1.0}) is None
    assert not log_path.exists()


def test_disabled_chain_does_not_report_intact(tmp_path, no_key_env):
    """"No records" and "records intact" must not print the same."""
    chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")

    assert chain.verify() is False
    valid, count, errors = chain.verify_log()
    assert valid is False
    assert count == 0
    assert errors and "no commitment key" in errors[0]
    assert "no commitment key" in chain.disabled_reason


def test_no_default_secret_literal_in_the_source():
    """The shipped key is gone from the module that used to return it.

    Read as bytes assembled at runtime so this assertion does not itself
    reintroduce the literal into the package.
    """
    source = Path(CommitmentChain.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[1] / source).read_text()
    literal = "opencastor-default-" + "commitment-secret"
    assert literal not in text


def test_secret_file_env_enables_the_chain(tmp_path, no_key_env, monkeypatch):
    """The generated path: `castor up` writes the key, the unit names the file."""
    key_file = tmp_path / "commitment.key"
    key_file.write_bytes(os.urandom(32))
    monkeypatch.setenv("OPENCASTOR_COMMITMENT_SECRET_FILE", str(key_file))

    chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")

    assert chain.enabled is True
    assert chain._secret == key_file.read_bytes().strip()
    assert chain.append_action("stop", {}) is not None


def test_unreadable_secret_file_fails_closed(tmp_path, no_key_env, monkeypatch):
    """A named but missing key file disables the chain; it does not invent one."""
    monkeypatch.setenv("OPENCASTOR_COMMITMENT_SECRET_FILE", str(tmp_path / "absent.key"))

    chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")

    assert chain.enabled is False
    assert chain.append_action("stop", {}) is None


def test_empty_secret_file_fails_closed(tmp_path, no_key_env, monkeypatch):
    key_file = tmp_path / "commitment.key"
    key_file.write_bytes(b"   \n")
    monkeypatch.setenv("OPENCASTOR_COMMITMENT_SECRET_FILE", str(key_file))

    chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")

    assert chain.enabled is False


def test_env_secret_wins_over_secret_file(tmp_path, no_key_env, monkeypatch):
    key_file = tmp_path / "commitment.key"
    key_file.write_bytes(b"from-the-file")
    monkeypatch.setenv("OPENCASTOR_COMMITMENT_SECRET", "from-the-env")
    monkeypatch.setenv("OPENCASTOR_COMMITMENT_SECRET_FILE", str(key_file))

    chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")

    assert chain._secret == b"from-the-env"


def test_castor_up_mints_the_key_the_unit_names(tmp_path, no_key_env):
    """The generator side of the same contract, checked end to end.

    `castor up` mints the key file at 0600; the generated castor unit names it
    through OPENCASTOR_COMMITMENT_SECRET_FILE; feeding that value back to the
    chain enables it. Nobody hand-edits a file anywhere in that loop.
    """
    from castor.up import COMMITMENT_KEY_FILE, mint_commitment_key

    home = tmp_path / "robot-home"
    home.mkdir()

    key_file, reused = mint_commitment_key(home)

    assert reused is False
    assert key_file == home / COMMITMENT_KEY_FILE
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert len(key_file.read_bytes()) == 32

    # Reused, never rotated: rotating orphans every record already sealed.
    again, reused_again = mint_commitment_key(home)
    assert reused_again is True
    assert again.read_bytes() == key_file.read_bytes()

    os.environ["OPENCASTOR_COMMITMENT_SECRET_FILE"] = str(key_file)
    try:
        chain = CommitmentChain(log_path=tmp_path / "commitments.jsonl")
        assert chain.enabled is True
    finally:
        del os.environ["OPENCASTOR_COMMITMENT_SECRET_FILE"]
