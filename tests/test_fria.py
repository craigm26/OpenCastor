"""Tests for castor/fria.py — FRIA document generation (§22)."""
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_result(check_id, category, status, detail="ok", fix=None):
    from castor.conformance import ConformanceResult
    return ConformanceResult(
        check_id=check_id,
        category=category,
        status=status,
        detail=detail,
        fix=fix,
    )


def _make_config(rrn="RRN-000000000001"):
    return {
        "rcan_version": "1.9.0",
        "metadata": {
            "rrn": rrn,
            "rrn_uri": "rrn://test/robot/model/001",
            "robot_name": "test-bot",
        },
        "agent": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    }


# ── check_fria_prerequisite ───────────────────────────────────────────────────

class TestCheckFriaPrerequisite:
    def _mock_checker(self, results, score):
        checker = MagicMock()
        checker.run_all.return_value = results
        checker.summary.return_value = {
            "pass": sum(1 for r in results if r.status == "pass"),
            "warn": sum(1 for r in results if r.status == "warn"),
            "fail": sum(1 for r in results if r.status == "fail"),
            "score": score,
        }
        return checker

    def test_passes_when_score_ok_and_no_safety_failures(self):
        from castor.fria import check_fria_prerequisite
        results = [
            _make_result("safety.estop_configured", "safety", "pass"),
            _make_result("protocol.rcan_version", "protocol", "pass"),
        ]
        with patch("castor.fria.ConformanceChecker", return_value=self._mock_checker(results, 90)):
            passed, blocking = check_fria_prerequisite(_make_config())
        assert passed is True
        assert blocking == []

    def test_blocked_by_low_score(self):
        from castor.fria import check_fria_prerequisite
        results = [
            _make_result("protocol.rcan_version", "protocol", "fail", fix="Update RCAN version"),
            _make_result("protocol.other", "protocol", "fail"),
        ]
        with patch("castor.fria.ConformanceChecker", return_value=self._mock_checker(results, 60)):
            passed, blocking = check_fria_prerequisite(_make_config())
        assert passed is False
        assert len(blocking) == 2

    def test_blocked_by_safety_failure_even_if_score_ok(self):
        from castor.fria import check_fria_prerequisite
        results = [
            _make_result("safety.estop_configured", "safety", "fail", fix="Configure ESTOP"),
            _make_result("protocol.rcan_version", "protocol", "pass"),
        ]
        with patch("castor.fria.ConformanceChecker", return_value=self._mock_checker(results, 85)):
            passed, blocking = check_fria_prerequisite(_make_config())
        assert passed is False
        assert any(r.check_id == "safety.estop_configured" for r in blocking)

    def test_passes_at_exact_boundary_score_80(self):
        from castor.fria import check_fria_prerequisite
        results = [
            _make_result("protocol.rcan_version", "protocol", "pass"),
        ]
        with patch("castor.fria.ConformanceChecker", return_value=self._mock_checker(results, 80)):
            passed, blocking = check_fria_prerequisite(_make_config())
        assert passed is True
        assert blocking == []


# ── build_fria_document ───────────────────────────────────────────────────────

class TestBuildFriaDocument:
    def _patched_checker(self, score=87):
        results = [
            _make_result("safety.estop_configured", "safety", "pass"),
            _make_result("safety.confidence_gates_configured", "safety", "warn", fix="Set threshold"),
        ]
        checker = MagicMock()
        checker.run_all.return_value = results
        checker.summary.return_value = {"pass": 1, "warn": 1, "fail": 0, "score": score}
        return checker

    def test_returns_dict_with_required_top_level_keys(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(_make_config(), "safety_component", "indoor nav")
        for key in ("schema", "spec_ref", "generated_at", "system", "deployment",
                    "conformance", "human_oversight", "hardware_observations"):
            assert key in doc, f"Missing top-level key: {key}"

    def test_schema_version_and_spec_ref(self):
        from castor.fria import FRIA_SCHEMA_VERSION, FRIA_SPEC_REF, build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(_make_config(), "safety_component", "indoor nav")
        assert doc["schema"] == FRIA_SCHEMA_VERSION
        assert doc["spec_ref"] == FRIA_SPEC_REF

    def test_system_fields_populated_from_config(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(_make_config(), "safety_component", "indoor nav")
        assert doc["system"]["rrn"] == "RRN-000000000001"
        assert doc["system"]["robot_name"] == "test-bot"
        assert doc["system"]["agent_provider"] == "anthropic"

    def test_deployment_fields(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(
                _make_config(), "safety_component", "indoor nav", prerequisite_waived=True
            )
        assert doc["deployment"]["annex_iii_basis"] == "safety_component"
        assert doc["deployment"]["intended_use"] == "indoor nav"
        assert doc["deployment"]["prerequisite_waived"] is True

    def test_conformance_score_present(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker(87)):
            doc = build_fria_document(_make_config(), "safety_component", "indoor nav")
        assert doc["conformance"]["score"] == 87
        assert isinstance(doc["conformance"]["checks"], list)
        assert len(doc["conformance"]["checks"]) == 2

    def test_raises_on_invalid_annex_iii_basis(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            with pytest.raises(ValueError, match="Invalid annex_iii_basis"):
                build_fria_document(_make_config(), "not_a_valid_basis", "indoor nav")

    def test_hardware_observations_empty_when_no_memory(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(_make_config(), "safety_component", "indoor nav", memory_path=None)
        assert doc["hardware_observations"] == []

    def test_hardware_observations_loaded_from_memory(self, tmp_path):
        """HARDWARE_OBSERVATION entries with confidence >= 0.30 are included."""
        from datetime import datetime

        from castor.brain.memory_schema import EntryType, MemoryEntry, RobotMemory, save_memory
        from castor.fria import build_fria_document

        now = datetime.now()
        memory = RobotMemory(
            schema_version="1.0",
            rrn="RRN-000000000001",
            last_updated=now,
            entries=[
                MemoryEntry(
                    id="mem-abc01",
                    type=EntryType.HARDWARE_OBSERVATION,
                    text="Left motor stalls",
                    confidence=0.82,
                    first_seen=now,
                    last_reinforced=now,
                    tags=["motor"],
                ),
                MemoryEntry(
                    id="mem-abc02",
                    type=EntryType.HARDWARE_OBSERVATION,
                    text="Low confidence obs",
                    confidence=0.10,  # below threshold
                    first_seen=now,
                    last_reinforced=now,
                    tags=[],
                ),
                MemoryEntry(
                    id="mem-abc03",
                    type=EntryType.ENVIRONMENT_NOTE,  # wrong type
                    text="Lab environment",
                    confidence=0.9,
                    first_seen=now,
                    last_reinforced=now,
                    tags=[],
                ),
            ],
        )
        mem_path = str(tmp_path / "robot-memory.md")
        save_memory(memory, mem_path)

        with patch("castor.fria.ConformanceChecker", return_value=self._patched_checker()):
            doc = build_fria_document(
                _make_config(), "safety_component", "indoor nav", memory_path=mem_path
            )
        obs = doc["hardware_observations"]
        assert len(obs) == 1
        assert obs[0]["id"] == "mem-abc01"
        assert obs[0]["text"] == "Left motor stalls"


# ── sign_fria ────────────────────────────────────────────────────────────────

class TestSignFria:
    def _make_doc(self):
        from castor.fria import FRIA_SCHEMA_VERSION, FRIA_SPEC_REF
        return {
            "schema": FRIA_SCHEMA_VERSION,
            "spec_ref": FRIA_SPEC_REF,
            "generated_at": "2026-04-10T14:32:01+00:00",
            "system": {"rrn": "RRN-000000000001", "robot_name": "bot"},
            "deployment": {"annex_iii_basis": "safety_component", "intended_use": "test"},
            "conformance": {"score": 87, "pass": 1, "warn": 0, "fail": 0, "checks": []},
            "human_oversight": {},
            "hardware_observations": [],
        }

    def _mock_signer(self, pub=b"\x01" * 32):
        signer = MagicMock()
        signer.public_key_bytes.return_value = pub
        signer._pq_key_id = "test-kid"
        pq_pair = MagicMock()
        pq_pair.sign_bytes.return_value = b"\xff" * 32
        pq_pair.key_id = "test-kid"
        signer._pq_key_pair = pq_pair
        return signer

    def test_adds_signing_key_and_sig_fields(self):
        from castor.fria import sign_fria
        signer = self._mock_signer()
        with patch("castor.fria.get_message_signer", return_value=signer):
            signed = sign_fria(self._make_doc(), _make_config())
        assert "signing_key" in signed
        assert "sig" in signed
        assert signed["signing_key"]["alg"] == "ml-dsa-65"
        assert signed["sig"]["alg"] == "ml-dsa-65"
        assert signed["sig"]["kid"] == "test-kid"

    def test_sig_value_is_base64url(self):
        import base64

        from castor.fria import sign_fria
        signer = self._mock_signer()
        with patch("castor.fria.get_message_signer", return_value=signer):
            signed = sign_fria(self._make_doc(), _make_config())
        value = signed["sig"]["value"]
        assert isinstance(value, str)
        base64.urlsafe_b64decode(value + "==")  # must not raise

    def test_sign_bytes_called_with_canonical_json(self):
        import json

        from castor.fria import sign_fria
        signer = self._mock_signer()
        with patch("castor.fria.get_message_signer", return_value=signer):
            sign_fria(self._make_doc(), _make_config())
        signer._pq_key_pair.sign_bytes.assert_called_once()
        call_arg = signer._pq_key_pair.sign_bytes.call_args[0][0]
        assert isinstance(call_arg, bytes)
        # The canonical payload must not contain a 'sig' key
        payload = json.loads(call_arg.decode())
        assert "sig" not in payload

    def test_raises_when_no_signer(self):
        from castor.fria import sign_fria
        with patch("castor.fria.get_message_signer", return_value=None):
            with pytest.raises(RuntimeError, match="No message signer"):
                sign_fria(self._make_doc(), _make_config())

    def test_raises_when_no_keypair(self):
        from castor.fria import sign_fria
        signer = MagicMock()
        signer._pq_key_pair = None
        signer.public_key_bytes.return_value = b"\x01" * 32
        with patch("castor.fria.get_message_signer", return_value=signer):
            with pytest.raises(RuntimeError, match="keypair"):
                sign_fria(self._make_doc(), _make_config())


# ── _load_benchmark_block / build_fria_document with benchmark ───────────────

class TestBuildFriaDocumentWithBenchmark:
    def _make_config(self) -> dict:
        return {
            "rcan_version": "1.9.0",
            "metadata": {
                "robot_name": "test-bot",
                "rrn": "RRN-000000000001",
            },
        }

    def _make_benchmark_file(self, tmp_path, overall_pass: bool = True) -> str:
        data = {
            "schema": "rcan-safety-benchmark-v1",
            "generated_at": "2026-04-11T09:00:00.000Z",
            "mode": "synthetic",
            "iterations": 20,
            "thresholds": {
                "estop_p95_ms": 100.0,
                "bounds_check_p95_ms": 5.0,
                "confidence_gate_p95_ms": 2.0,
                "full_pipeline_p95_ms": 50.0,
            },
            "results": {
                "estop": {"min_ms": 0.3, "mean_ms": 1.2, "p95_ms": 4.1, "p99_ms": 7.2, "max_ms": 9.8, "pass": True},
                "bounds_check": {"min_ms": 0.1, "mean_ms": 0.4, "p95_ms": 0.9, "p99_ms": 1.1, "max_ms": 1.4, "pass": True},
                "confidence_gate": {"min_ms": 0.05, "mean_ms": 0.1, "p95_ms": 0.3, "p99_ms": 0.4, "max_ms": 0.5, "pass": True},
                "full_pipeline": {"min_ms": 0.4, "mean_ms": 1.8, "p95_ms": 5.2, "p99_ms": 8.1, "max_ms": 11.0, "pass": True},
            },
            "overall_pass": overall_pass,
        }
        import json
        path = tmp_path / "safety-benchmark-20260411.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_benchmark_inlined_when_path_provided(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=MagicMock(
            run_all=lambda: [],
            summary=lambda r: {"pass": 0, "warn": 0, "fail": 0, "score": 87},
        )):
            doc = build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=bench_path,
            )
        assert "safety_benchmarks" in doc

    def test_benchmark_block_has_required_fields(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=MagicMock(
            run_all=lambda: [],
            summary=lambda r: {"pass": 0, "warn": 0, "fail": 0, "score": 87},
        )):
            doc = build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=bench_path,
            )
        sb = doc["safety_benchmarks"]
        for field in ("ref", "generated_at", "mode", "overall_pass", "results"):
            assert field in sb

    def test_benchmark_omitted_when_path_is_none(self):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=MagicMock(
            run_all=lambda: [],
            summary=lambda r: {"pass": 0, "warn": 0, "fail": 0, "score": 87},
        )):
            doc = build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=None,
            )
        assert "safety_benchmarks" not in doc

    def test_benchmark_omitted_when_file_missing(self, tmp_path):
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=MagicMock(
            run_all=lambda: [],
            summary=lambda r: {"pass": 0, "warn": 0, "fail": 0, "score": 87},
        )):
            doc = build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=str(tmp_path / "nonexistent.json"),
            )
        assert "safety_benchmarks" not in doc

    def test_invalid_schema_raises_value_error(self, tmp_path):
        import json
        from castor.fria import build_fria_document
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"schema": "wrong-schema", "results": {}}))
        with pytest.raises(ValueError, match="schema"):
            build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=str(bad_file),
            )

    def test_ref_field_contains_filename(self, tmp_path):
        bench_path = self._make_benchmark_file(tmp_path)
        from castor.fria import build_fria_document
        with patch("castor.fria.ConformanceChecker", return_value=MagicMock(
            run_all=lambda: [],
            summary=lambda r: {"pass": 0, "warn": 0, "fail": 0, "score": 87},
        )):
            doc = build_fria_document(
                config=self._make_config(),
                annex_iii_basis="safety_component",
                intended_use="Indoor navigation",
                benchmark_path=bench_path,
            )
        assert "safety-benchmark-20260411.json" in doc["safety_benchmarks"]["ref"]


# ── render_fria_html ──────────────────────────────────────────────────────────

class TestRenderFriaHtml:
    def _make_full_doc(self):
        return {
            "schema": "rcan-fria-v1",
            "spec_ref": "https://rcan.dev/spec/section-22",
            "generated_at": "2026-04-10T14:32:01+00:00",
            "system": {
                "rrn": "RRN-000000000001",
                "rrn_uri": "rrn://test/robot/model/001",
                "robot_name": "test-bot",
                "opencastor_version": "2026.4.10.0",
                "rcan_version": "1.9.0",
                "agent_provider": "anthropic",
                "agent_model": "claude-sonnet-4-6",
            },
            "deployment": {
                "annex_iii_basis": "safety_component",
                "intended_use": "indoor navigation",
                "prerequisite_waived": False,
            },
            "conformance": {
                "score": 87,
                "pass": 10,
                "warn": 2,
                "fail": 0,
                "checks": [
                    {"check_id": "safety.estop_configured", "category": "safety",
                     "status": "pass", "detail": "ESTOP configured"},
                ],
            },
            "human_oversight": {
                "hitl_configured": True,
                "confidence_gates_configured": True,
                "estop_configured": True,
            },
            "hardware_observations": [
                {"id": "mem-abc01", "text": "Left motor stalls", "confidence": 0.82, "tags": ["motor"]},
            ],
            "signing_key": {"alg": "ml-dsa-65", "kid": "test-kid", "public_key": "abc123"},
            "sig": {"alg": "ml-dsa-65", "kid": "test-kid", "value": "sig-value"},
        }

    def test_returns_string_containing_rrn(self):
        from castor.fria import render_fria_html
        html = render_fria_html(self._make_full_doc())
        assert isinstance(html, str)
        assert "RRN-000000000001" in html

    def test_contains_annex_iii_basis(self):
        from castor.fria import render_fria_html
        html = render_fria_html(self._make_full_doc())
        assert "safety_component" in html

    def test_contains_spec_ref(self):
        from castor.fria import render_fria_html
        html = render_fria_html(self._make_full_doc())
        assert "rcan.dev/spec/section-22" in html

    def test_contains_conformance_score(self):
        from castor.fria import render_fria_html
        html = render_fria_html(self._make_full_doc())
        assert "87" in html

    def test_contains_hardware_observation(self):
        from castor.fria import render_fria_html
        html = render_fria_html(self._make_full_doc())
        assert "Left motor stalls" in html

    def test_renders_without_sig_field(self):
        """--skip-sign path: doc has no 'sig' key; template must not crash."""
        from castor.fria import render_fria_html
        doc = self._make_full_doc()
        del doc["sig"]
        del doc["signing_key"]
        html = render_fria_html(doc)
        assert isinstance(html, str)
        assert "RRN-000000000001" in html
