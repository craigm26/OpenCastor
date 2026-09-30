"""Tests for RCAN §21 Registry Framework (REGISTRY_REGISTER / REGISTRY_RESOLVE)."""

import pytest

from castor.rcan.message import MessageType
from castor.rcan.registry import (
    RegistryMessage,
    RegistryRegisterResult,
    RegistryResolveRequest,
    RegistryResolveResponse,
    RegistryResolveResult,
    RRNCategory,
    _parse_rrn,
    _validate_rrn,
)


class TestRegistryMessageType:
    def test_registry_register_value(self):
        assert MessageType.REGISTRY_REGISTER == 14

    def test_registry_resolve_value(self):
        assert MessageType.REGISTRY_RESOLVE == 15

    def test_registry_register_name_lookup(self):
        assert MessageType["REGISTRY_REGISTER"] is MessageType.REGISTRY_REGISTER

    def test_registry_resolve_name_lookup(self):
        assert MessageType["REGISTRY_RESOLVE"] is MessageType.REGISTRY_RESOLVE


class TestRegistryMessageRoundTrip:
    def test_to_message_type(self):
        msg = RegistryMessage(
            msg_id="m-001",
            rrn="rrn://example.org/rover-1",
            ruri="rcan://192.168.1.10:8000/rover-1",
            public_key="-----BEGIN PUBLIC KEY-----\nMFYw...\n-----END PUBLIC KEY-----",
        )
        raw = msg.to_message()
        assert raw["type"] == MessageType.REGISTRY_REGISTER

    def test_to_message_payload_fields(self):
        msg = RegistryMessage(
            msg_id="m-002",
            rrn="rrn://example.org/arm-1",
            ruri="rcan://arm.local:8000/arm-1",
            public_key="pk-placeholder",
            timestamp=1700000000.0,
        )
        raw = msg.to_message()
        payload = raw["payload"]
        assert payload["rrn"] == "rrn://example.org/arm-1"
        assert payload["ruri"] == "rcan://arm.local:8000/arm-1"
        assert payload["public_key"] == "pk-placeholder"
        assert payload["timestamp"] == 1700000000.0

    def test_from_message_round_trip(self):
        original = RegistryMessage(
            msg_id="m-003",
            rrn="rrn://example.org/rover-2",
            ruri="rcan://rover2.local:8000/rover-2",
            public_key="pk-data",
            timestamp=1700001234.5,
        )
        raw = original.to_message()
        restored = RegistryMessage.from_message(raw)
        assert restored.rrn == original.rrn
        assert restored.ruri == original.ruri
        assert restored.public_key == original.public_key
        assert restored.timestamp == original.timestamp

    def test_from_message_msg_id_preserved(self):
        original = RegistryMessage(
            msg_id="m-004",
            rrn="rrn://x/y",
            ruri="rcan://x:8000/y",
            public_key="pk",
        )
        raw = original.to_message()
        restored = RegistryMessage.from_message(raw)
        assert restored.msg_id == "m-004"


class TestRegistryMessageMissingFields:
    def test_missing_rrn_raises(self):
        with pytest.raises(ValueError, match="rrn"):
            RegistryMessage.from_message(
                {"msg_id": "x", "payload": {"ruri": "rcan://x", "public_key": "pk"}}
            )

    def test_missing_ruri_raises(self):
        with pytest.raises(ValueError, match="ruri"):
            RegistryMessage.from_message(
                {
                    "msg_id": "x",
                    "payload": {
                        "rrn": "rrn://x/y",
                        "public_key": "pk",
                    },
                }
            )

    def test_missing_public_key_raises(self):
        with pytest.raises(ValueError, match="public_key"):
            RegistryMessage.from_message(
                {
                    "msg_id": "x",
                    "payload": {"rrn": "rrn://x/y", "ruri": "rcan://x"},
                }
            )


class TestRegistryResolveRequest:
    def test_to_message_type(self):
        req = RegistryResolveRequest(rrn="rrn://example.org/rover-1")
        raw = req.to_message()
        assert raw["type"] == MessageType.REGISTRY_RESOLVE

    def test_to_message_rrn_in_payload(self):
        req = RegistryResolveRequest(rrn="rrn://example.org/arm-2", msg_id="req-001")
        raw = req.to_message()
        assert raw["payload"]["rrn"] == "rrn://example.org/arm-2"
        assert raw["msg_id"] == "req-001"

    def test_auto_generated_msg_id(self):
        req1 = RegistryResolveRequest(rrn="rrn://x/y")
        req2 = RegistryResolveRequest(rrn="rrn://x/y")
        assert req1.msg_id != req2.msg_id


class TestRegistryResolveResponse:
    def test_to_message_type(self):
        resp = RegistryResolveResponse(
            rrn="rrn://example.org/rover-1",
            ruri="rcan://rover1.local:8000/rover-1",
            verified=True,
            tier="pro",
        )
        raw = resp.to_message()
        assert raw["type"] == MessageType.REGISTRY_RESOLVE

    def test_to_message_all_fields(self):
        resp = RegistryResolveResponse(
            rrn="rrn://example.org/arm-1",
            ruri="rcan://arm1.local:8000/arm-1",
            verified=False,
            tier="free",
        )
        raw = resp.to_message()
        payload = raw["payload"]
        assert payload["rrn"] == "rrn://example.org/arm-1"
        assert payload["ruri"] == "rcan://arm1.local:8000/arm-1"
        assert payload["verified"] is False
        assert payload["tier"] == "free"


# ── §21.4 REGISTRY_REGISTER_RESULT ───────────────────────────────────────────


class TestRegistryRegisterResultMessageType:
    def test_register_result_enum_value(self):
        # §3.2 has no separate result types; the name is a deprecated alias of RESPONSE.
        assert MessageType.REGISTRY_REGISTER_RESULT is MessageType.RESPONSE

    def test_resolve_result_enum_value(self):
        assert MessageType.REGISTRY_RESOLVE_RESULT is MessageType.RESPONSE


class TestRegistryRegisterResult:
    def test_success_to_message_type(self):
        result = RegistryRegisterResult(
            msg_id="r-001",
            status="success",
            rrn="rrn://example.org/rover-1",
        )
        raw = result.to_message()
        assert raw["type"] == MessageType.REGISTRY_REGISTER_RESULT

    def test_success_payload_fields(self):
        result = RegistryRegisterResult(
            msg_id="r-002",
            status="success",
            rrn="rrn://example.org/arm-1",
        )
        raw = result.to_message()
        assert raw["payload"]["status"] == "success"
        assert raw["payload"]["rrn"] == "rrn://example.org/arm-1"
        assert "error" not in raw["payload"]

    def test_failure_payload_fields(self):
        result = RegistryRegisterResult(
            msg_id="r-003",
            status="failure",
            error="RRN already registered by another owner",
        )
        raw = result.to_message()
        assert raw["payload"]["status"] == "failure"
        assert raw["payload"]["error"] == "RRN already registered by another owner"
        assert "rrn" not in raw["payload"]

    def test_from_message_round_trip_success(self):
        original = RegistryRegisterResult(
            msg_id="r-004",
            status="success",
            rrn="rrn://example.org/rover-2",
        )
        restored = RegistryRegisterResult.from_message(original.to_message())
        assert restored.msg_id == "r-004"
        assert restored.status == "success"
        assert restored.rrn == "rrn://example.org/rover-2"

    def test_from_message_round_trip_failure(self):
        original = RegistryRegisterResult(
            msg_id="r-005",
            status="failure",
            error="auth failed",
        )
        restored = RegistryRegisterResult.from_message(original.to_message())
        assert restored.status == "failure"
        assert restored.error == "auth failed"
        assert restored.rrn is None

    def test_from_message_missing_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            RegistryRegisterResult.from_message({"msg_id": "x", "payload": {}})


# ── §21.5 REGISTRY_RESOLVE_RESULT ────────────────────────────────────────────


class TestRegistryResolveResult:
    def test_found_to_message_type(self):
        result = RegistryResolveResult(
            msg_id="rr-001",
            status="found",
            rrn="rrn://example.org/rover-1",
            ruri="rcan://rover1.local:8000/rover-1",
            verified=True,
            tier="pro",
        )
        raw = result.to_message()
        assert raw["type"] == MessageType.REGISTRY_RESOLVE_RESULT

    def test_found_payload_fields(self):
        result = RegistryResolveResult(
            msg_id="rr-002",
            status="found",
            rrn="rrn://example.org/arm-1",
            ruri="rcan://arm1.local:8000/arm-1",
            verified=False,
            tier="free",
        )
        raw = result.to_message()
        p = raw["payload"]
        assert p["status"] == "found"
        assert p["rrn"] == "rrn://example.org/arm-1"
        assert p["ruri"] == "rcan://arm1.local:8000/arm-1"
        assert p["verified"] is False
        assert p["tier"] == "free"

    def test_not_found_payload(self):
        result = RegistryResolveResult(
            msg_id="rr-003",
            status="not_found",
            rrn="rrn://example.org/robots/unknown",
            error="No robot registered with this RRN",
        )
        raw = result.to_message()
        p = raw["payload"]
        assert p["status"] == "not_found"
        assert p["error"] == "No robot registered with this RRN"
        assert "ruri" not in p

    def test_auth_failure_payload(self):
        result = RegistryResolveResult(
            msg_id="rr-004",
            status="auth_failure",
            rrn="rrn://example.org/robots/secure-bot",
            error="Authentication token rejected",
        )
        raw = result.to_message()
        assert raw["payload"]["status"] == "auth_failure"
        assert raw["payload"]["error"] == "Authentication token rejected"

    def test_from_message_round_trip_found(self):
        original = RegistryResolveResult(
            msg_id="rr-005",
            status="found",
            rrn="rrn://example.org/robots/rover-3",
            ruri="rcan://rover3.local:8000/rover-3",
            verified=True,
            tier="enterprise",
        )
        restored = RegistryResolveResult.from_message(original.to_message())
        assert restored.msg_id == "rr-005"
        assert restored.status == "found"
        assert restored.rrn == "rrn://example.org/robots/rover-3"
        assert restored.ruri == "rcan://rover3.local:8000/rover-3"
        assert restored.verified is True
        assert restored.tier == "enterprise"

    def test_from_message_round_trip_not_found(self):
        original = RegistryResolveResult(
            msg_id="rr-006",
            status="not_found",
            rrn="rrn://example.org/robots/ghost",
            error="Not registered",
        )
        restored = RegistryResolveResult.from_message(original.to_message())
        assert restored.status == "not_found"
        assert restored.error == "Not registered"
        assert restored.ruri is None

    def test_from_message_missing_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            RegistryResolveResult.from_message({"payload": {"rrn": "rrn://x/y"}})

    def test_from_message_missing_rrn_raises(self):
        with pytest.raises(ValueError, match="rrn"):
            RegistryResolveResult.from_message({"payload": {"status": "found"}})


# ── RRN Format Validation ─────────────────────────────────────────────────────


class TestRRNValidation:
    # ── Basic format checks ───────────────────────────────────────────────
    def test_legacy_two_segment_passes(self):
        _validate_rrn("rrn://example.org/rover-1")  # legacy 2-segment

    def test_three_segment_passes(self):
        _validate_rrn("rrn://example.org/rover-1")  # 3-segment

    def test_four_segment_structured_passes(self):
        _validate_rrn("rrn://opencastor.com/robot/v2/unit-001")  # full structured

    def test_valid_rrn_short_host(self):
        _validate_rrn("rrn://myorg/bot-1")  # no exception

    def test_empty_rrn_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_rrn("")

    def test_missing_prefix_raises(self):
        with pytest.raises(ValueError, match="rrn://"):
            _validate_rrn("rcan://example.org/robots/rover-1")

    def test_http_prefix_raises(self):
        with pytest.raises(ValueError, match="rrn://"):
            _validate_rrn("http://example.org/robots/rover-1")

    def test_no_path_raises(self):
        with pytest.raises(ValueError):
            _validate_rrn("rrn://example.org")

    def test_empty_segment_raises(self):
        with pytest.raises(ValueError):
            _validate_rrn("rrn://example.org/")

    def test_five_segments_raises(self):
        with pytest.raises(ValueError):
            _validate_rrn("rrn://org/robot/model/id/extra")

    # ── Category validation ───────────────────────────────────────────────
    def test_valid_categories_pass(self):
        for cat in ("robot", "component", "sensor", "assembly"):
            _validate_rrn(f"rrn://opencastor.com/{cat}/unit-001")

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="category"):
            _validate_rrn("rrn://opencastor.com/vehicle/unit-001")

    def test_four_segment_invalid_category_raises(self):
        with pytest.raises(ValueError, match="category"):
            _validate_rrn("rrn://opencastor.com/drone/v2/unit-001")

    # ── Integration with dataclasses ──────────────────────────────────────
    def test_rrn_validation_in_registry_message(self):
        with pytest.raises(ValueError, match="rrn://"):
            RegistryMessage(
                msg_id="m-bad",
                rrn="not-a-valid-rrn",
                ruri="rcan://x:8000/y",
                public_key="pk",
            )

    def test_rrn_validation_in_resolve_request(self):
        with pytest.raises(ValueError, match="rrn://"):
            RegistryResolveRequest(rrn="invalid-rrn-format")

    def test_structured_rrn_in_registry_message_passes(self):
        msg = RegistryMessage(
            msg_id="m-ok",
            rrn="rrn://opencastor.com/robot/v2/unit-001",
            ruri="rcan://rover.local:8000/unit-001",
            public_key="pk",
        )
        assert msg.rrn == "rrn://opencastor.com/robot/v2/unit-001"
        assert msg.category == RRNCategory.ROBOT

    def test_component_rrn_category_parsed(self):
        msg = RegistryMessage(
            msg_id="m-comp",
            rrn="rrn://opencastor.com/component/hailo8/module-42",
            ruri="rcan://hailo.local:8000/module-42",
            public_key="pk",
        )
        assert msg.category == RRNCategory.COMPONENT

    def test_legacy_rrn_category_is_none(self):
        msg = RegistryMessage(
            msg_id="m-legacy",
            rrn="rrn://example.org/rover-1",
            ruri="rcan://rover.local:8000/rover-1",
            public_key="pk",
        )
        assert msg.category is None  # legacy format has no category segment


# ── _parse_rrn ────────────────────────────────────────────────────────────────


class TestParseRRN:
    def test_four_segment_full(self):
        result = _parse_rrn("rrn://opencastor.com/robot/v2/unit-001")
        assert result == {
            "org": "opencastor.com",
            "category": "robot",
            "model": "v2",
            "id": "unit-001",
        }

    def test_three_segment(self):
        result = _parse_rrn("rrn://example.org/robot/rover-1")
        assert result == {
            "org": "example.org",
            "category": "robot",
            "model": None,
            "id": "rover-1",
        }

    def test_two_segment_legacy(self):
        result = _parse_rrn("rrn://example.org/rover-1")
        assert result == {
            "org": "example.org",
            "category": None,
            "model": None,
            "id": "rover-1",
        }

    def test_component_four_segment(self):
        result = _parse_rrn("rrn://luxonis.com/sensor/oak-d/cam-007")
        assert result["org"] == "luxonis.com"
        assert result["category"] == "sensor"
        assert result["model"] == "oak-d"
        assert result["id"] == "cam-007"


# ── RRNCategory enum ──────────────────────────────────────────────────────────


class TestRRNCategory:
    def test_all_categories_have_string_values(self):
        assert RRNCategory.ROBOT == "robot"
        assert RRNCategory.COMPONENT == "component"
        assert RRNCategory.SENSOR == "sensor"
        assert RRNCategory.ASSEMBLY == "assembly"

    def test_category_from_string(self):
        assert RRNCategory("robot") is RRNCategory.ROBOT
        assert RRNCategory("sensor") is RRNCategory.SENSOR


# ── RegistryMessage.metadata ──────────────────────────────────────────────────


class TestRegistryMessageMetadata:
    def test_metadata_round_trip(self):
        meta = {
            "model": "OpenCastor v2",
            "serial": "OC2-2026-001",
            "manufacturer": "opencastor.com",
            "firmware": "v2026.3.13.10",
            "components": [
                "rrn://opencastor.com/component/hailo8/module-42",
                "rrn://luxonis.com/sensor/oak-d/cam-007",
            ],
        }
        msg = RegistryMessage(
            msg_id="m-meta",
            rrn="rrn://opencastor.com/robot/v2/unit-001",
            ruri="rcan://rover.local:8000/unit-001",
            public_key="pk",
            metadata=meta,
        )
        raw = msg.to_message()
        assert raw["payload"]["metadata"] == meta

    def test_metadata_preserved_in_from_message(self):
        meta = {"serial": "ABC-123", "parent_rrn": "rrn://org/assembly/stack/asm-1"}
        original = RegistryMessage(
            msg_id="m-meta2",
            rrn="rrn://org/component/hailo8/chip-9",
            ruri="rcan://chip9.local:8000/chip-9",
            public_key="pk",
            metadata=meta,
        )
        restored = RegistryMessage.from_message(original.to_message())
        assert restored.metadata == meta

    def test_empty_metadata_omitted_from_payload(self):
        """Empty metadata dict should not appear in the serialised payload."""
        msg = RegistryMessage(
            msg_id="m-nometa",
            rrn="rrn://example.org/rover-1",
            ruri="rcan://rover.local:8000/rover-1",
            public_key="pk",
        )
        raw = msg.to_message()
        assert "metadata" not in raw["payload"]

    def test_from_message_no_metadata_returns_empty_dict(self):
        original = RegistryMessage(
            msg_id="m-none",
            rrn="rrn://example.org/rover-2",
            ruri="rcan://rover.local:8000/rover-2",
            public_key="pk",
        )
        restored = RegistryMessage.from_message(original.to_message())
        assert restored.metadata == {}


# ── RegistryResolveResponse.from_message ─────────────────────────────────────


class TestRegistryResolveResponseFromMessage:
    def test_from_message_round_trip(self):
        original = RegistryResolveResponse(
            rrn="rrn://example.org/rover-1",
            ruri="rcan://rover1.local:8000/rover-1",
            verified=True,
            tier="pro",
        )
        restored = RegistryResolveResponse.from_message(original.to_message())
        assert restored.rrn == original.rrn
        assert restored.ruri == original.ruri
        assert restored.verified is True
        assert restored.tier == "pro"

    def test_from_message_missing_field_raises(self):
        with pytest.raises(ValueError, match="ruri"):
            RegistryResolveResponse.from_message(
                {"payload": {"rrn": "rrn://x/y", "verified": True, "tier": "free"}}
            )
