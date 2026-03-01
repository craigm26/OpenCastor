"""Tests for visual geofence editor.

Issue #203 — GeofencePolygon, polygon RCAN config persistence, HTML template.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# GeofencePolygon
# ---------------------------------------------------------------------------


class TestGeofencePolygon:
    _SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    def test_init_basic(self):
        from castor.geofence import GeofencePolygon

        p = GeofencePolygon("yard", self._SQUARE)
        assert p.name == "yard"
        assert len(p.points) == 4
        assert p.action == "stop"

    def test_init_custom_action(self):
        from castor.geofence import GeofencePolygon

        p = GeofencePolygon("zone", self._SQUARE, action="warn")
        assert p.action == "warn"

    def test_requires_three_points(self):
        from castor.geofence import GeofencePolygon

        with pytest.raises(ValueError, match="at least 3"):
            GeofencePolygon("bad", [[0, 0], [1, 0]])

    def test_contains_centre(self):
        from castor.geofence import GeofencePolygon

        poly = GeofencePolygon("sq", self._SQUARE)
        assert poly.contains(0.5, 0.5) is True

    def test_does_not_contain_exterior(self):
        from castor.geofence import GeofencePolygon

        poly = GeofencePolygon("sq", self._SQUARE)
        assert poly.contains(2.0, 2.0) is False

    def test_contains_near_edge(self):
        from castor.geofence import GeofencePolygon

        poly = GeofencePolygon("sq", self._SQUARE)
        assert poly.contains(0.01, 0.01) is True

    def test_to_dict(self):
        from castor.geofence import GeofencePolygon

        poly = GeofencePolygon("yard", self._SQUARE, action="warn")
        d = poly.to_dict()
        assert d["name"] == "yard"
        assert d["action"] == "warn"
        assert len(d["points"]) == 4

    def test_from_dict(self):
        from castor.geofence import GeofencePolygon

        d = {"name": "zone", "action": "stop", "points": self._SQUARE}
        poly = GeofencePolygon.from_dict(d)
        assert poly.name == "zone"
        assert len(poly.points) == 4

    def test_repr_contains_name(self):
        from castor.geofence import GeofencePolygon

        poly = GeofencePolygon("myzone", self._SQUARE)
        assert "myzone" in repr(poly)


# ---------------------------------------------------------------------------
# load_polygons
# ---------------------------------------------------------------------------


class TestLoadPolygons:
    _SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    def test_loads_from_config(self):
        from castor.geofence import load_polygons

        config = {
            "geofence": {
                "enabled": True,
                "polygons": [{"name": "z1", "points": self._SQUARE}],
            }
        }
        polys = load_polygons(config)
        assert len(polys) == 1
        assert polys[0].name == "z1"

    def test_empty_when_no_polygons(self):
        from castor.geofence import load_polygons

        polys = load_polygons({})
        assert polys == []

    def test_skips_invalid_polygon(self):
        from castor.geofence import load_polygons

        config = {
            "geofence": {
                "polygons": [
                    {"name": "bad", "points": [[0, 0], [1, 1]]},  # only 2 points
                    {"name": "ok", "points": self._SQUARE},
                ]
            }
        }
        polys = load_polygons(config)
        assert len(polys) == 1
        assert polys[0].name == "ok"


# ---------------------------------------------------------------------------
# save_polygon_to_config
# ---------------------------------------------------------------------------


class TestSavePolygonToConfig:
    _SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    def _write_cfg(self, tmp_path, data=None):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text(yaml.dump(data or {"rcan_version": "1.1.0"}))
        return str(cfg)

    def test_saves_polygon(self, tmp_path):
        from castor.geofence import GeofencePolygon, save_polygon_to_config

        cfg_path = self._write_cfg(tmp_path)
        poly = GeofencePolygon("yard", self._SQUARE)
        save_polygon_to_config(cfg_path, poly)

        data = yaml.safe_load(Path(cfg_path).read_text())
        assert len(data["geofence"]["polygons"]) == 1
        assert data["geofence"]["polygons"][0]["name"] == "yard"

    def test_replaces_existing_polygon(self, tmp_path):
        from castor.geofence import GeofencePolygon, save_polygon_to_config

        cfg_path = self._write_cfg(
            tmp_path,
            {
                "rcan_version": "1.1.0",
                "geofence": {"polygons": [{"name": "yard", "points": self._SQUARE}]},
            },
        )
        updated = [[0, 0], [2, 0], [2, 2], [0, 2]]
        save_polygon_to_config(cfg_path, GeofencePolygon("yard", updated))

        data = yaml.safe_load(Path(cfg_path).read_text())
        assert len(data["geofence"]["polygons"]) == 1  # not doubled
        assert data["geofence"]["polygons"][0]["points"][1] == [2, 0]


# ---------------------------------------------------------------------------
# delete_polygon_from_config
# ---------------------------------------------------------------------------


class TestDeletePolygonFromConfig:
    _SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    def _write_cfg(self, tmp_path, data):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text(yaml.dump(data))
        return str(cfg)

    def test_deletes_polygon(self, tmp_path):
        from castor.geofence import delete_polygon_from_config

        cfg_path = self._write_cfg(
            tmp_path,
            {
                "geofence": {
                    "polygons": [
                        {"name": "yard", "points": self._SQUARE},
                        {"name": "other", "points": self._SQUARE},
                    ]
                }
            },
        )
        result = delete_polygon_from_config(cfg_path, "yard")
        assert result is True

        data = yaml.safe_load(Path(cfg_path).read_text())
        names = [p["name"] for p in data["geofence"]["polygons"]]
        assert "yard" not in names
        assert "other" in names

    def test_returns_false_for_nonexistent(self, tmp_path):
        from castor.geofence import delete_polygon_from_config

        cfg_path = self._write_cfg(
            tmp_path,
            {"geofence": {"polygons": [{"name": "yard", "points": self._SQUARE}]}},
        )
        result = delete_polygon_from_config(cfg_path, "nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# geofence.html template
# ---------------------------------------------------------------------------


class TestGeofenceHTMLTemplate:
    def _html(self):
        p = Path("castor/templates/geofence.html")
        assert p.exists(), "castor/templates/geofence.html must exist"
        return p.read_text()

    def test_template_exists(self):
        assert Path("castor/templates/geofence.html").exists()

    def test_includes_leaflet_css(self):
        html = self._html()
        assert "leaflet" in html.lower()

    def test_includes_map_div(self):
        html = self._html()
        assert 'id="map"' in html

    def test_includes_draw_polygon(self):
        html = self._html()
        assert "polygon" in html.lower()

    def test_includes_save_api_call(self):
        html = self._html()
        assert "/api/geofence/polygon" in html

    def test_includes_config_path_input(self):
        html = self._html()
        assert "config-path" in html or "config_path" in html
