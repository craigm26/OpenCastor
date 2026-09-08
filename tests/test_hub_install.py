"""`castor hub install` must not report success for work it did not do.

It used to return the DESTINATION path whether or not it had copied anything
into it, so the CLI printed "✅ Installed to ./config.rcan.yaml" for a file
that was never written — and every one of the seven shipped recipes was in
exactly that state, because none of them had a config.rcan.yaml at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from castor.hub import RECIPES_DIR, RecipeInstallError, install_recipe, list_recipes

CONFIG_BODY = "rcan_version: '3.0'\nmetadata:\n  robot_name: x\n"


def _make_recipe(root: Path, rid="fake-1234", *, with_config=True, docs=()):
    d = root / rid
    d.mkdir(parents=True)
    (d / "recipe.json").write_text(
        json.dumps(
            {
                "id": rid,
                "name": "Fake",
                "files": {"config": "config.rcan.yaml", "readme": "README.md",
                          "docs": list(docs)},
            }
        )
    )
    (d / "README.md").write_text("# Fake\n")
    if with_config:
        (d / "config.rcan.yaml").write_text(CONFIG_BODY)
    return d


@pytest.fixture
def fake_recipes(tmp_path, monkeypatch):
    root = tmp_path / "community-recipes"
    root.mkdir()
    monkeypatch.setattr("castor.hub.RECIPES_DIR", root)
    return root


def test_install_writes_the_config_and_returns_its_real_path(fake_recipes, tmp_path):
    _make_recipe(fake_recipes)
    dest = tmp_path / "out"
    result = install_recipe("fake-1234", dest=str(dest))
    assert result == dest / "config.rcan.yaml"
    assert result.exists()
    assert result.read_text() == CONFIG_BODY


def test_install_copies_the_readme_under_a_recipe_specific_name(fake_recipes, tmp_path):
    _make_recipe(fake_recipes)
    dest = tmp_path / "out"
    install_recipe("fake-1234", dest=str(dest))
    assert (dest / "README-fake-1234.md").exists()


def test_a_recipe_that_names_a_config_it_does_not_ship_raises(fake_recipes, tmp_path):
    _make_recipe(fake_recipes, with_config=False)
    dest = tmp_path / "out"
    with pytest.raises(RecipeInstallError) as exc:
        install_recipe("fake-1234", dest=str(dest))
    assert "config.rcan.yaml" in str(exc.value)
    assert "Nothing was installed" in str(exc.value)


def test_a_failed_install_leaves_no_file_behind(fake_recipes, tmp_path):
    _make_recipe(fake_recipes, with_config=False)
    dest = tmp_path / "out"
    with pytest.raises(RecipeInstallError):
        install_recipe("fake-1234", dest=str(dest))
    assert not (dest / "config.rcan.yaml").exists()


def test_an_unknown_recipe_still_returns_none(fake_recipes, tmp_path):
    assert install_recipe("no-such-recipe", dest=str(tmp_path)) is None


def test_a_missing_doc_warns_but_still_installs(fake_recipes, tmp_path, capsys):
    _make_recipe(fake_recipes, docs=["BUILD_NOTES.md"])
    dest = tmp_path / "out"
    result = install_recipe("fake-1234", dest=str(dest))
    assert result.exists()
    assert "BUILD_NOTES.md" in capsys.readouterr().out


# ── the seven shipped recipes ────────────────────────────────────────────────


SHIPPED = sorted(
    p.parent.name for p in RECIPES_DIR.glob("*/recipe.json")
)


def test_there_are_seven_shipped_recipes():
    assert len(SHIPPED) == 7


@pytest.mark.parametrize("rid", SHIPPED)
def test_every_shipped_recipe_ships_every_file_it_names(rid):
    manifest = json.loads((RECIPES_DIR / rid / "recipe.json").read_text())
    files = manifest["files"]
    for name in [files["config"], files.get("readme", "README.md")] + files.get("docs", []):
        assert (RECIPES_DIR / rid / name).exists(), f"{rid} names {name} but does not ship it"


@pytest.mark.parametrize("rid", SHIPPED)
def test_every_shipped_recipe_installs_for_real(rid, tmp_path):
    dest = tmp_path / rid
    result = install_recipe(rid, dest=str(dest))
    assert result is not None and result.exists()
    cfg = yaml.safe_load(result.read_text())
    assert cfg["rcan_version"] == "3.0"
    assert cfg["metadata"]["robot_name"]
    assert cfg["agent"]["provider"] and cfg["agent"]["model"]


def test_shipped_recipe_identities_do_not_collide():
    """Two owners of the same kit must not end up as the same robot."""
    uuids, rrns = set(), set()
    for rid in SHIPPED:
        cfg = yaml.safe_load((RECIPES_DIR / rid / "config.rcan.yaml").read_text())
        meta = cfg["metadata"]
        assert meta["robot_uuid"] not in uuids, f"{rid} reuses a robot_uuid"
        assert meta["rrn"] not in rrns, f"{rid} reuses an RRN"
        uuids.add(meta["robot_uuid"])
        rrns.add(meta["rrn"])


def test_the_rc_car_recipe_is_ackermann_not_differential():
    """No preset has the RC-car shape, so this one is cut from the rc_car template.

    A differential config on a servo-steered car steers by trying to spin one
    side faster than the other, which a steering servo cannot do.
    """
    cfg = yaml.safe_load(
        (RECIPES_DIR / "picar-home-patrol-e7f3a1" / "config.rcan.yaml").read_text()
    )
    assert cfg["physics"]["type"] == "ackermann"
    assert "drive.set" in cfg["rcan_protocol"]["capabilities"]
    assert "drive.stop" in cfg["rcan_protocol"]["capabilities"]
    # The runtime's own driver stays a mock: the gateway's rc-car actuator owns
    # the wheels, and a second path to them is outside the deadman.
    assert cfg["drivers"][0]["protocol"] == "simulation"


def test_every_shipped_recipe_config_is_listed_by_list_recipes():
    ids = {r["id"] for r in list_recipes()}
    assert ids == set(SHIPPED)
