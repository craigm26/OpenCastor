import time

from castor.world import EntityRecord, WaypointRecord, WorldModel


def test_conflict_resolution_prefers_higher_confidence_and_fresher():
    model = WorldModel()
    old = EntityRecord(
        entity_id="charger-a",
        kind="charger",
        confidence=0.4,
        observed_at=time.time() - 60,
        attrs={"label": "charger"},
    )
    new = EntityRecord(
        entity_id="charger-a",
        kind="charger",
        confidence=0.9,
        observed_at=time.time(),
        attrs={"label": "charger"},
    )
    model.merge("objects", old)
    winner = model.merge("objects", new)
    assert winner.confidence == 0.9


def test_last_seen_returns_latest_match():
    model = WorldModel()
    model.merge(
        "objects",
        EntityRecord(
            entity_id="charger-old",
            kind="charger",
            observed_at=time.time() - 100,
            attrs={"label": "charger"},
        ),
    )
    model.merge(
        "objects",
        EntityRecord(
            entity_id="charger-new",
            kind="charger",
            observed_at=time.time(),
            attrs={"label": "charger"},
        ),
    )
    assert model.last_seen("charger").entity_id == "charger-new"


def test_safe_route_avoids_blocked_zone():
    model = WorldModel(
        waypoints={
            "A": WaypointRecord(entity_id="A", kind="waypoint", neighbors=["B", "C"]),
            "B": WaypointRecord(
                entity_id="B", kind="waypoint", neighbors=["D"], zone_ids=["child"]
            ),
            "C": WaypointRecord(entity_id="C", kind="waypoint", neighbors=["D"]),
            "D": WaypointRecord(entity_id="D", kind="waypoint", neighbors=[]),
        }
    )
    assert model.safe_route("A", "D", avoid_zones=["child"]) == ["A", "C", "D"]
