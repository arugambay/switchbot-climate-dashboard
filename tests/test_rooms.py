"""Tests für Raumnamen-Normalisierung, Ähnlichkeit und Vorschläge."""

from __future__ import annotations

import pytest

from climate_dashboard.domain.models import Room, utcnow
from climate_dashboard.domain.rooms import (
    find_similar_rooms,
    is_duplicate_name,
    normalize_room_name,
    suggest_room_name_from_filename,
    suggest_sensor_name_from_filename,
    validate_room_name,
)


def rooms() -> list[Room]:
    return [
        Room(id="1", name="Schlafzimmer"),
        Room(id="2", name="Küche"),
        Room(id="3", name="Keller links", archived_at=utcnow()),
        Room(id="4", name="Wohnzimmer"),
    ]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("  Küche ", "kueche"),
        ("Schlaf   Zimmer", "schlaf zimmer"),
        ("GROSSES Bad", "grosses bad"),
        ("Straße", "strasse"),
        ("Bäder\tÖl", "baeder oel"),
    ],
)
def test_normalize(raw, normalized):
    assert normalize_room_name(raw) == normalized


def test_validate():
    assert validate_room_name("") is not None
    assert validate_room_name("   ") is not None
    assert validate_room_name("x" * 81) is not None
    assert validate_room_name("x" * 80) is None
    assert validate_room_name("Bad") is None


def test_similar_exact_normalized_is_one():
    result = find_similar_rooms("  kueche", rooms())
    assert [(r.id, s) for r, s in result] == [("2", 1.0)]


def test_similar_fuzzy_and_archived_flagged():
    result = find_similar_rooms("Schlafzimmmer", rooms())
    assert result[0][0].id == "1" and 0.8 <= result[0][1] < 1.0
    archived = find_similar_rooms("Keller-links", rooms())
    assert archived and archived[0][0].is_archived


def test_similar_nothing_below_threshold():
    assert find_similar_rooms("Dachboden", rooms()) == []
    assert find_similar_rooms("", rooms()) == []


def test_similar_never_merges():
    rs = rooms()
    find_similar_rooms("Küche", rs)
    assert [r.name for r in rs] == ["Schlafzimmer", "Küche", "Keller links", "Wohnzimmer"]


def test_is_duplicate_name():
    assert is_duplicate_name("KÜCHE ", rooms())
    assert is_duplicate_name("keller   LINKS", rooms())  # auch archivierte Räume
    assert not is_duplicate_name("Küche", rooms(), exclude_id="2")  # Umbenennen auf sich selbst
    assert not is_duplicate_name("Küche 2", rooms())


@pytest.mark.parametrize(
    ("file_name", "room", "sensor"),
    [
        ("Schlafzimmer_Sensor_20260301_20261022.csv", "Schlafzimmer", "Schlafzimmer Sensor"),
        ("Keller-links_export.csv", "Keller links", "Keller links"),
        ("C:/Downloads/Bad_Meter_2026-01-01.csv", "Bad", "Bad Meter"),
        ("Arbeitszimmer.CSV", "Arbeitszimmer", "Arbeitszimmer"),
        ("export_20260101.csv", None, None),
        ("20260101_20260301.csv", None, None),
        ("", None, None),
    ],
)
def test_name_suggestions(file_name, room, sensor):
    assert suggest_room_name_from_filename(file_name) == room
    assert suggest_sensor_name_from_filename(file_name) == sensor


def test_no_forced_standard_names():
    # Beliebige Namen bleiben erhalten, keine Abbildung auf Standardräume
    assert suggest_room_name_from_filename("Omas Wintergarten_data.csv") == "Omas Wintergarten"
    assert suggest_room_name_from_filename("Raum 3b_export.csv") == "Raum 3b"
