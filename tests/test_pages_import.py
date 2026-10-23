"""Tests für den Import-Assistenten: Logik (``ui/import_flow.py``) und Rendern der Seite."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from climate_dashboard.domain.models import AnalysisSettings, ImportStatus
from climate_dashboard.ingest.validation import Severity
from climate_dashboard.ui import import_flow as flow
from tests.fake_repo import FakeRepository
from tests.synthetic import make_measurements, to_switchbot_csv

ROOT = Path(__file__).resolve().parent.parent


def csv(start="2026-01-05", end="2026-01-08", **kw) -> bytes:
    return to_switchbot_csv(make_measurements(start, end, interval_min=30, **kw))


# ---------------------------------------------------------------------------
# Logik
# ---------------------------------------------------------------------------
def test_new_entry_suggestions_only():
    e = flow.new_entry("Schlafzimmer_Sensor_20260301_20261022.csv", b"x")
    assert e.room_mode == flow.NEW and e.new_room_name == "Schlafzimmer"
    assert e.new_sensor_name == "Schlafzimmer Sensor"
    assert e.timezone == "Europe/Berlin"
    e2 = flow.new_entry("export_20260101.csv", b"y")
    assert e2.new_room_name == ""  # kein erzwungener Standardname
    assert e2.new_sensor_name == flow.DEFAULT_SENSOR_NAME


def test_add_files_skips_identical_content():
    entries, skipped = flow.add_files([], [("a.csv", b"1"), ("b.csv", b"1"), ("c.csv", b"2")])
    assert [e.name for e in entries] == ["a.csv", "c.csv"] and skipped == ["b.csv"]
    entries, skipped = flow.add_files(entries, [("a.csv", b"1")])
    assert len(entries) == 2 and skipped == ["a.csv"]


def test_similar_room_hint_and_explicit_choice_never_auto_merge():
    repo = FakeRepository()
    kitchen = repo.create_room("Küche")
    e = flow.new_entry("Kueche_export.csv", b"x")
    hints = flow.similar_room_hints(e, repo.list_rooms(include_archived=True))
    assert [(r.id, s) for r, s in hints] == [(kitchen.id, 1.0)]
    assert e.room_mode == flow.NEW  # nichts automatisch übernommen
    assert flow.room_errors(e, repo.list_rooms()) != []  # gleicher Name als neuer Raum unzulässig
    flow.use_existing_room(e, kitchen)
    assert e.room_mode == flow.EXISTING and e.room_id == kitchen.id
    assert flow.room_errors(e, repo.list_rooms()) == []


def test_room_and_sensor_validation():
    e = flow.new_entry("export.csv", b"x")
    assert flow.room_errors(e, []) == ["Bitte einen Namen für den Raum eingeben."]
    e.new_room_name = "Bad"
    assert flow.room_errors(e, []) == []
    e.new_sensor_name = " "
    assert flow.sensor_errors(e, []) == ["Bitte einen Namen für den neuen Sensor eingeben."]
    repo = FakeRepository()
    r1, r2 = repo.create_room("A"), repo.create_room("B")
    s2 = repo.create_sensor(r2.id, "S")
    e.room_mode, e.room_id = flow.EXISTING, r1.id
    e.sensor_mode, e.sensor_id = flow.EXISTING, s2.id
    assert flow.sensor_errors(e, repo.list_sensors()) == [
        "Der gewählte Sensor gehört nicht zum gewählten Raum."
    ]


@pytest.mark.parametrize(
    ("label", "value", "unit", "expected"),
    [
        ("Automatisch erkennen", 0, "Minuten", None),
        ("15 Minuten", 0, "Minuten", 900),
        ("24 Stunden", 0, "Minuten", 86400),
        (flow.CUSTOM_INTERVAL, 10, "Minuten", 600),
        (flow.CUSTOM_INTERVAL, 2, "Stunden", 7200),
        (flow.CUSTOM_INTERVAL, 90, "Sekunden", 90),
    ],
)
def test_interval_seconds(label, value, unit, expected):
    e = flow.new_entry("x.csv", b"x")
    e.interval_label, e.custom_interval_value, e.custom_interval_unit = label, value, unit
    assert flow.interval_seconds(e) == expected


def test_interval_label_roundtrip():
    assert flow.interval_label_for(900) == ("15 Minuten", 15.0, "Minuten")
    assert flow.interval_label_for(600) == (flow.CUSTOM_INTERVAL, 10, "Minuten")
    assert flow.interval_label_for(7200) == (flow.CUSTOM_INTERVAL, 2, "Stunden")


def test_assignment_table():
    repo = FakeRepository()
    room = repo.create_room("Keller")
    sensor = repo.create_sensor(room.id, "Hygrometer")
    a = flow.new_entry("Bad_export.csv", b"1")
    b = flow.new_entry("k.csv", b"2")
    b.room_mode, b.room_id, b.sensor_mode, b.sensor_id = flow.EXISTING, room.id, flow.EXISTING, sensor.id
    rows = flow.assignment_rows([a, b], repo.list_rooms(), repo.list_sensors())
    assert rows == [
        {"Datei": "Bad_export.csv", "Raum": "Bad (neu)", "Sensor": "Bad (neu)"},
        {"Datei": "k.csv", "Raum": "Keller", "Sensor": "Hygrometer"},
    ]


def test_preview_cache_parses_once_per_key(monkeypatch):
    calls = []
    original = flow.preview_import

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(flow, "preview_import", counting)
    repo, cache = FakeRepository(), {}
    e = flow.new_entry("Bad.csv", csv())
    settings = AnalysisSettings()
    p1 = flow.get_preview(cache, repo, e, [], settings)
    p2 = flow.get_preview(cache, repo, e, [], settings)
    assert p1 is p2 and len(calls) == 1
    e.interval_label = "15 Minuten"
    flow.get_preview(cache, repo, e, [], settings)
    assert len(calls) == 2 and calls[-1]["expected_interval_s"] == 900
    assert calls[-1]["sensor_id"] is None and calls[-1]["settings"] is settings


def test_nothing_created_before_confirmation_and_shared_new_targets():
    repo = FakeRepository()
    a = flow.new_entry("Bad_Teil1.csv", csv("2026-01-05", "2026-01-07"))
    b = flow.new_entry("Bad_Teil2.csv", csv("2026-01-06", "2026-01-09", seed=2))
    a.new_room_name = b.new_room_name = "Bad"
    a.new_sensor_name = b.new_sensor_name = "Bad Sensor"
    settings = AnalysisSettings()
    previews = {e.file_id: flow.get_preview({}, repo, e, [], settings) for e in (a, b)}
    assert repo.list_rooms() == [] and repo.list_sensors() == []  # Vorschau legt nichts an
    assert flow.blocking_reasons([a, b], [], [], previews) == []
    overlap = [i for i in flow.session_issues([a, b], previews) if i.code == "files_overlap"]
    assert len(overlap) == 1 and overlap[0].severity == Severity.INFO

    events = []
    outcomes = flow.run_import(repo, [a, b], previews, progress=lambda f, t: events.append(f))
    assert len(repo.list_rooms()) == 1 and len(repo.list_sensors()) == 1  # nur einmal angelegt
    assert all(o.record and o.record.status == ImportStatus.COMPLETED for o in outcomes)
    first, second = outcomes
    assert first.record.rows_new == 96
    assert second.record.rows_new == 144 - 48  # Überlappung (06.–07.01.) erkannt
    # andere Zufallswerte (seed) → überwiegend Konflikte (zufällig gleiche Werte = Duplikat), nichts überschrieben
    assert second.record.rows_conflict + second.record.rows_duplicate == 48
    assert second.record.rows_conflict > 40
    assert events[-1] == 1.0 and events == sorted(events)
    sensor = repo.list_sensors()[0]
    assert sensor.name == "Bad Sensor" and sensor.timezone == "Europe/Berlin"
    summary = flow.outcome_summary(first)
    assert summary["Status"] == "abgeschlossen" and summary["Neu"] == 96


def test_blocking_reasons_for_errors():
    repo = FakeRepository()
    bad = flow.new_entry("Bad.csv", b"timestamp,humidity\n2026-01-01 10:00,50\n")
    previews = {bad.file_id: flow.get_preview({}, repo, bad, [], AnalysisSettings())}
    reasons = flow.blocking_reasons([bad], [], [], previews)
    assert reasons == ["Bad.csv: Die Datei enthält Fehler und kann nicht importiert werden."]
    assert flow.blocking_reasons([], [], [], {}) == ["Es wurde noch keine Datei ausgewählt."]


def test_failed_file_does_not_stop_others():
    repo = FakeRepository(fail_after_batches=1)
    a = flow.new_entry("A.csv", csv("2026-01-05", "2026-01-06"))
    b = flow.new_entry("B.csv", csv("2026-02-05", "2026-02-06"))
    b.new_room_name = "B"
    previews = {e.file_id: flow.get_preview({}, repo, e, [], AnalysisSettings()) for e in (a, b)}
    outcomes = flow.run_import(repo, [a, b], previews)
    assert outcomes[0].record is not None and outcomes[1].record is None
    assert "rückgängig" in outcomes[1].error
    assert flow.outcome_summary(outcomes[1])["Status"] == "fehlgeschlagen"


def test_group_issues_order():
    from climate_dashboard.ingest.validation import Issue

    groups = flow.group_issues(
        [Issue(Severity.INFO, "i", "x"), Issue(Severity.ERROR, "e", "y"), Issue(Severity.WARNING, "w", "z")]
    )
    assert list(groups) == [Severity.ERROR, Severity.WARNING, Severity.INFO]
    assert [len(v) for v in groups.values()] == [1, 1, 1]


# ---------------------------------------------------------------------------
# Seite (AppTest)
# ---------------------------------------------------------------------------
@pytest.fixture
def local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "wizard.sqlite"))
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.chdir(ROOT)


def _page():
    from climate_dashboard.ui import theme
    from climate_dashboard.ui.pages import import_wizard

    theme.apply_plotly_template()
    theme.inject_css("app")
    import_wizard.render()


def _html(at: AppTest) -> str:
    return "\n".join(el.proto.body for el in at.get("html"))


def _with_state(step: int, entries: list) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["imp_wizard"] = {
        "step": step,
        "entries": entries,
        "cache": {},
        "previews": {},
        "outcomes": [],
        "uploader": 0,
    }
    return at.run()


def test_page_renders_empty(local_mode):
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception
    body = _html(at)
    assert "cd-steps" in body and "Noch keine Datei ausgewählt" in body
    assert len(at.get("file_uploader")) == 1
    weiter = [b for b in at.button if b.label == "Weiter"]
    assert weiter and weiter[0].disabled


def test_page_steps_with_injected_file(local_mode):
    entry = flow.new_entry("Bad_export.csv", csv())
    # Schritt 2: Raum – Vorschlag aus Dateiname im Textfeld
    at = _with_state(1, [entry])
    assert not at.exception
    assert at.text_input(key=f"imp_roomname_{entry.file_id}").value == "Bad"
    # Schritt 5: Vorschau
    at = _with_state(4, [entry])
    assert not at.exception
    body = _html(at)
    assert "Zeitabdeckung" in body and "Komma" in body
    assert len(at.get("plotly_chart")) == 1
    # Schritt 6: Prüfung, Schritt 7: Bestätigung (Button aktiv, nichts angelegt)
    at = _with_state(5, [entry])
    assert not at.exception and "Gelesen" in _html(at)
    at = _with_state(6, [entry])
    assert not at.exception
    start = [b for b in at.button if b.label == "Import starten"][0]
    assert not start.disabled
    start.click().run()
    assert not at.exception
    body = _html(at)
    assert "Import abgeschlossen" in body
    assert at.session_state["imp_wizard"]["step"] == 7


def test_page_import_button_disabled_on_errors(local_mode):
    entry = flow.new_entry("Bad.csv", b"timestamp,humidity\n2026-01-01 10:00,50\n")
    at = _with_state(6, [entry])
    assert not at.exception
    start = [b for b in at.button if b.label == "Import starten"][0]
    assert start.disabled


def test_page_similar_room_hint_and_use_existing(local_mode, tmp_path):
    from climate_dashboard.storage.sqlite_repo import SqliteRepository

    SqliteRepository(tmp_path / "wizard.sqlite").create_room("Bad")
    entry = flow.new_entry("Bad_export.csv", csv())
    at = _with_state(1, [entry])
    assert not at.exception
    assert "Meinst du den vorhandenen Raum „Bad“?" in _html(at)
    weiter = [b for b in at.button if b.label == "Weiter"][0]
    assert weiter.disabled  # gleicher Name als neuer Raum nicht erlaubt – keine automatische Übernahme
    [b for b in at.button if b.label == "Vorhandenen Raum verwenden"][0].click().run()
    assert not at.exception
    assert at.radio(key=f"imp_roommode_{entry.file_id}").value == "Vorhandener Raum"
    assert not [b for b in at.button if b.label == "Weiter"][0].disabled
