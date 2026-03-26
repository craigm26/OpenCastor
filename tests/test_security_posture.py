import json

from castor.fs import CastorFS
from castor.security_posture import detect_attestation_status, publish_attestation


def test_detect_attestation_from_env(monkeypatch, tmp_path):
    # Clear any file-based attestation so env vars are the sole source.
    # Also patch the default paths to non-existent locations so a leftover
    # /tmp/opencastor_attestation.json cannot contaminate this test.
    monkeypatch.delenv("OPENCASTOR_ATTESTATION_PATH", raising=False)
    monkeypatch.setattr(
        "castor.security_posture._DEFAULT_ATTESTATION_PATHS",
        (str(tmp_path / "no-proc.json"), str(tmp_path / "no-run.json")),
    )
    monkeypatch.setenv("OPENCASTOR_SECURE_BOOT", "1")
    monkeypatch.setenv("OPENCASTOR_MEASURED_BOOT", "0")
    monkeypatch.setenv("OPENCASTOR_SIGNED_UPDATES", "1")

    result = detect_attestation_status()

    assert result["verified"] is False
    assert result["mode"] == "degraded"
    assert "measured_boot_unavailable" in result["reasons"]


def test_detect_attestation_from_file(tmp_path, monkeypatch):
    payload_path = tmp_path / "attestation.json"
    payload_path.write_text(
        json.dumps(
            {
                "secure_boot": True,
                "measured_boot": True,
                "signed_updates": True,
                "verified": True,
                "token": "abc123",
                "profile": "secure",
            }
        )
    )
    monkeypatch.setenv("OPENCASTOR_ATTESTATION_PATH", str(payload_path))

    result = detect_attestation_status()

    assert result["verified"] is True
    assert result["mode"] == "enforced"
    assert result["token"] == "abc123"


def test_publish_attestation_writes_proc_safety(monkeypatch):
    monkeypatch.setenv("OPENCASTOR_SECURE_BOOT", "0")
    monkeypatch.setenv("OPENCASTOR_SIGNED_UPDATES", "0")

    fs = CastorFS()
    fs.boot({"metadata": {"robot_name": "t"}, "agent": {}, "rcan_protocol": {}})

    posture = publish_attestation(fs)

    assert posture is not None
    assert fs.ns.read("/proc/safety/mode") == "degraded"
    assert fs.ns.read("/proc/safety/attestation_status") == "degraded"
    assert isinstance(fs.ns.read("/proc/safety"), dict)
