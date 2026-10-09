"""The safety.workspace config block (castor.safety.workspace.parse_workspace_config).

Absent means off; a valid block builds the workspace policy; an invalid block is refused with a
message that names every problem. With no pose source, through the config path too, every
translating move is refused: OpenCastor ships no localizer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema
import pytest

from castor.fs import CastorFS
from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import SafetyLayer
from castor.safety.workspace import (
    _OPTIONAL_KEYS,
    _REQUIRED_KEYS,
    WorkspaceConfigError,
    parse_workspace_config,
    register_pose_source,
    unregister_pose_source,
)

POSE_SOURCE = "test-workspace-config"
BAY = [[0, 0], [6, 0], [6, 4], [0, 4]]
VALID = {
    "keep_in": BAY,
    "keep_out": [[[2, 1], [3, 1], [3, 2], [2, 2]]],
    "top_speed_mps": 1.5,
    "max_decel_mps2": 1.0,
    "reaction_s": 0.04,
    "margin_m": 0.02,
    "enforce_hz": 50,
    "pose_source": POSE_SOURCE,
}
MINIMAL = {"keep_in": BAY, "top_speed_mps": 1.5, "max_decel_mps2": 1.0}
SCHEMA = Path(__file__).resolve().parents[1] / "config" / "rcan.schema.json"


@pytest.fixture(autouse=True)
def _no_pose_source_left_behind():
    yield
    unregister_pose_source(POSE_SOURCE)


def _without(key):
    return {k: v for k, v in VALID.items() if k != key}


def _safety(cfg) -> SafetyLayer:
    return SafetyLayer(
        Namespace(),
        PermissionTable(),
        limits={"motor_rate_hz": 1000.0},
        workspace_policy=cfg.build_policy(),
    )


# ----- absent and valid -------------------------------------------------------------------------
def test_an_absent_block_means_no_workspace_policy():
    assert parse_workspace_config(None) is None


def test_a_valid_block_is_parsed():
    cfg = parse_workspace_config(VALID)
    ws = cfg.workspace
    assert ws.keep_in == [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)]
    assert ws.keep_out == [[(2.0, 1.0), (3.0, 1.0), (3.0, 2.0), (2.0, 2.0)]]
    assert (ws.top_speed_mps, ws.max_decel_mps2) == (1.5, 1.0)
    assert (ws.reaction_s, ws.margin_m) == (0.04, 0.02)
    assert cfg.enforce_hz == 50.0
    assert cfg.pose_source == POSE_SOURCE


def test_defaults_for_the_optional_keys():
    cfg = parse_workspace_config(MINIMAL)
    assert cfg.workspace.keep_out == []
    assert (cfg.workspace.reaction_s, cfg.workspace.margin_m) == (0.05, 0.05)
    assert cfg.enforce_hz == 50.0
    assert cfg.pose_source is None


def test_a_valid_block_builds_a_working_policy():
    register_pose_source(POSE_SOURCE, lambda: (1.0, 3.0, 0.0, 0.0))
    policy = parse_workspace_config(VALID).build_policy()
    assert policy.check({"type": "move", "linear": 0.2, "angular": 0.0}) == (True, "")
    register_pose_source(POSE_SOURCE, lambda: (5.95, 3.0, 0.0, 0.0))  # restarted, at the wall
    assert policy.check({"type": "move", "linear": 0.2, "angular": 0.0})[0] is False


def test_castorfs_hands_the_policy_to_its_safety_layer():
    policy = parse_workspace_config(VALID).build_policy()
    assert CastorFS(workspace_policy=policy).safety.workspace_policy is policy
    assert CastorFS().safety.workspace_policy is None


# ----- invalid ----------------------------------------------------------------------------------
INVALID = [
    pytest.param([1, 2], "must be a mapping", id="not-a-mapping"),
    pytest.param("keep_in: []", "must be a mapping", id="a-string"),
    pytest.param(_without("keep_in"), "keep_in is required", id="no-keep_in"),
    pytest.param(_without("top_speed_mps"), "top_speed_mps is required", id="no-top-speed"),
    pytest.param(_without("max_decel_mps2"), "max_decel_mps2 is required", id="no-decel"),
    pytest.param({**VALID, "top_speed": 1.5}, "unknown key(s) top_speed", id="misspelt-key"),
    pytest.param({**VALID, "keep_in": [[0, 0], [6, 0]]}, "at least three", id="two-points"),
    pytest.param({**VALID, "keep_in": [[0, 0], [1, 1], [2, 2]]}, "no area", id="flat-polygon"),
    pytest.param({**VALID, "keep_in": [[0, 0], [6, "x"], [6, 4]]}, "keep_in[1]", id="str-coord"),
    pytest.param({**VALID, "keep_in": [[0, 0], [6], [6, 4]]}, "keep_in[1]", id="short-point"),
    pytest.param({**VALID, "keep_out": [[0, 0], [1, 0]]}, "keep_out[0]", id="keep_out-flat"),
    pytest.param({**VALID, "keep_out": "stairwell"}, "keep_out must be a list", id="keep_out-str"),
    pytest.param({**VALID, "top_speed_mps": 0}, "top_speed_mps must be a positive", id="zero"),
    pytest.param({**VALID, "top_speed_mps": True}, "top_speed_mps must be", id="bool-speed"),
    pytest.param({**VALID, "max_decel_mps2": math.nan}, "max_decel_mps2 must be", id="nan"),
    pytest.param({**VALID, "max_decel_mps2": "1.0"}, "max_decel_mps2 must be", id="str-number"),
    pytest.param({**VALID, "reaction_s": -0.1}, "reaction_s must be a positive", id="neg-react"),
    pytest.param({**VALID, "margin_m": -0.01}, "margin_m must be a non-negative", id="neg-margin"),
    pytest.param({**VALID, "enforce_hz": 0}, "enforce_hz must be", id="zero-hz"),
    pytest.param({**VALID, "enforce_hz": 5000}, "enforce_hz must be", id="busy-loop-hz"),
    pytest.param({**VALID, "enforce_hz": 10}, "must cover one enforcement period", id="slow-hz"),
    pytest.param({**VALID, "pose_source": ""}, "pose_source must be", id="empty-pose-source"),
    pytest.param({**VALID, "pose_source": 7}, "pose_source must be", id="int-pose-source"),
]


@pytest.mark.parametrize("block,needle", INVALID)
def test_an_invalid_block_is_refused_with_a_clear_error(block, needle):
    with pytest.raises(WorkspaceConfigError) as excinfo:
        parse_workspace_config(block)
    message = str(excinfo.value)
    assert message.startswith("safety.workspace")
    assert needle in message


def test_every_problem_is_named_at_once():
    block = {"keep_in": [[0, 0]], "top_speed": 2, "max_decel_mps2": -1, "enforce_hz": "fast"}
    with pytest.raises(WorkspaceConfigError) as excinfo:
        parse_workspace_config(block)
    message = str(excinfo.value)
    for needle in ("unknown key(s) top_speed", "top_speed_mps is required", "keep_in must be"):
        assert needle in message
    assert "max_decel_mps2 must be" in message and "enforce_hz must be" in message


def test_the_error_is_a_value_error():
    with pytest.raises(ValueError):
        parse_workspace_config({})


# ----- failing closed without a pose ------------------------------------------------------------
def test_with_no_pose_source_configured_translation_is_refused_and_the_rest_works():
    sl = _safety(parse_workspace_config(MINIMAL))  # no pose_source at all
    move = {"type": "move", "linear": 0.2, "angular": 0.3}
    assert sl.write("/dev/motor", move, principal="brain") is False
    assert "no pose" in sl.last_write_denial
    assert sl.ns.read("/dev/motor") == {"type": "move", "linear": 0.0, "angular": 0.3}
    turn = {"type": "move", "linear": 0.0, "angular": 0.5}
    assert sl.write("/dev/motor", turn, principal="brain") is True
    assert sl.write("/dev/motor", {"type": "stop"}, principal="brain") is True


def _raises():
    raise RuntimeError("tracker lost the marker")


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(None, id="nothing-registered"),
        pytest.param(lambda: None, id="no-fix"),
        pytest.param(_raises, id="raises"),
        pytest.param(lambda: (math.nan, 1.0, 0.0, 0.0), id="nan"),
        pytest.param(lambda: (1.0, 2.0), id="short"),
    ],
)
def test_a_named_pose_source_without_a_usable_pose_fails_closed(provider):
    if provider is not None:
        register_pose_source(POSE_SOURCE, provider)
    sl = _safety(parse_workspace_config(VALID))
    assert (
        sl.write("/dev/motor", {"type": "move", "linear": 0.2, "angular": 0.0}, principal="brain")
        is False
    )
    assert sl.ns.read("/dev/motor")["linear"] == 0.0


def test_unregistering_the_pose_source_refuses_motion_again():
    register_pose_source(POSE_SOURCE, lambda: (1.0, 3.0, 0.0, 0.0))
    sl = _safety(parse_workspace_config(VALID))
    move = {"type": "move", "linear": 0.2, "angular": 0.0}
    assert sl.write("/dev/motor", move, principal="brain") is True
    unregister_pose_source(POSE_SOURCE)
    assert sl.write("/dev/motor", dict(move), principal="brain") is False


def test_register_pose_source_checks_its_arguments():
    with pytest.raises(ValueError):
        register_pose_source("", lambda: None)
    with pytest.raises(TypeError):
        register_pose_source(POSE_SOURCE, (1.0, 2.0, 0.0, 0.0))


# ----- the schema documents the same block ------------------------------------------------------
def test_the_rcan_schema_documents_the_same_keys():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    block = schema["properties"]["safety"]["properties"]["workspace"]
    assert set(block["properties"]) == set(_REQUIRED_KEYS + _OPTIONAL_KEYS)
    assert set(block["required"]) == set(_REQUIRED_KEYS)
    validator = jsonschema.validators.validator_for(schema)(block)
    assert list(validator.iter_errors(VALID)) == []
    assert list(validator.iter_errors({**VALID, "top_speed": 1.5}))
