"""AppTest-Tests der Dashboard-Seiten (Übersicht, Analyse, Vergleich, Räume, Einstellungen, Anmeldung).

Nur synthetische Daten (``tests/synthetic.py``) in einer temporären SQLite-Datei.
"""

from __future__ import annotations

import pytest

from climate_dashboard.domain.models import ConflictResolution
from tests.test_pages_helpers import all_text, local_db, run_page, seed


@pytest.fixture
def db(tmp_path, monkeypatch):
    yield from local_db(tmp_path, monkeypatch)


PAGES = ["overview", "analysis", "compare", "rooms", "settings", "login"]


def _repo(path):
    from climate_dashboard.storage.sqlite_repo import SqliteRepository

    return SqliteRepository(str(path))


def _no_exception(at) -> None:
    assert not at.exception, [e.message for e in at.exception]


def _button(at, label: str):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"Button {label!r} nicht gefunden: {[b.label for b in at.button]}"
    return matches[0]


@pytest.mark.parametrize("module", PAGES)
def test_pages_render_without_data(db, module):
    at = run_page(module)
    _no_exception(at)


@pytest.mark.parametrize("module", PAGES)
def test_pages_render_with_data(db, module):
    seed(db, second_room=True, conflict=True)
    at = run_page(module)
    _no_exception(at)
    if module in ("overview", "analysis", "compare"):
        assert len(at.get("plotly_chart")) >= 1


def test_overview_shows_room_cards_and_stale_hint(db):
    seed(db, second_room=True)
    at = run_page("overview")
    _no_exception(at)
    text = all_text(at)
    assert "Schlafzimmer" in text and "Bad" in text
    assert "Daten nicht aktuell" in text  # letzte Messung liegt > 2 Tage zurück
    assert "Einschätzung der letzten 30 Tage" in text
    assert "Letzte 7 Tage vs. 7 Tage davor" in text


def test_empty_overview_points_to_import(db):
    at = run_page("overview")
    _no_exception(at)
    assert "Noch keine Daten" in all_text(at)


def test_analysis_full_period_all_tabs(db):
    seed(db, long=True)
    at = run_page("analysis")
    at.get("button_group")[0].set_value("Gesamt").run()
    _no_exception(at)
    text = all_text(at)
    for expected in ("Feuchtephasen", "Monate", "Jahreszeiten", "Datenqualität", "Lückenliste",
                     "Plausibilität", "Kriterien der Einschätzung"):  # fmt: skip
        assert expected in text, expected
    assert "unvollständig" in text.lower() or "Monatsanfang fehlt" in text


def test_analysis_surface_assumption_shows_annahme(db):
    seed(db)
    at = run_page("analysis")
    assert "ANNAHME" not in all_text(at)
    at.toggle(key="an_surface_on").set_value(True).run()
    _no_exception(at)
    at.number_input(key="an_surface_offset").set_value(6.0).run()
    _no_exception(at)
    text = all_text(at)
    assert "ANNAHME" in text
    assert "minus 6 K angenommen" in text
    assert "Oberfläche (Annahme)" in text


def test_compare_two_periods_shows_deltas(db):
    seed(db)
    at = run_page("compare")
    at.radio(key="cmp_mode").set_value("Zwei Zeiträume").run()
    _no_exception(at)
    frames = [df.value for df in at.dataframe]
    table = next(f for f in frames if "Δ (B − A)" in f.columns)
    temp = table[table["Kennzahl"] == "Mittlere Temperatur"].iloc[0]
    assert temp["Δ (B − A)"].endswith("K") and temp["Δ (B − A)"][0] in "+−±"
    assert table[table["Kennzahl"] == "Mittlere relative Luftfeuchtigkeit"].iloc[0]["Δ (B − A)"].endswith(
        "Prozentpunkte"
    )
    assert "Keine Ursachenaussage" in all_text(at)


@pytest.mark.parametrize(
    "mode", ["Tag / Woche / Monat", "Gleich lange Zeiträume", "Gleicher Monat in Vorjahren", "Jahreszeiten",
             "Raum / Sensor"],  # fmt: skip
)
def test_compare_modes_render(db, mode):
    seed(db, long=True, second_room=True)
    at = run_page("compare")
    at.radio(key="cmp_mode").set_value(mode).run()
    _no_exception(at)
    if mode == "Gleicher Monat in Vorjahren":
        assert "Nur ein Jahr vorhanden" in all_text(at)


def test_rename_room_keeps_measurements(db):
    ids = seed(db)
    at = run_page("rooms")
    at.text_input(key=f"room_name_{ids['room_id']}").set_value("Elternschlafzimmer")
    _button(at, "Umbenennen").click().run()
    _no_exception(at)
    repo = _repo(db)
    room = repo.get_room(ids["room_id"])
    assert room.name == "Elternschlafzimmer"
    assert repo.measurement_extent(ids["sensor_id"])[2] == ids["n"]
    assert [s.id for s in repo.list_sensors(room.id)] == [ids["sensor_id"]]
    assert "umbenannt" in all_text(at)


def test_similar_room_name_shows_hint_and_does_not_create(db):
    seed(db)
    at = run_page("rooms")
    at.text_input(key="room_new_name").set_value("schlafzimmer ")
    _button(at, "Raum anlegen").click().run()
    _no_exception(at)
    # gleicher normalisierter Name eines aktiven Raums → nicht anlegen
    assert "Name bereits vergeben" in all_text(at)
    at.text_input(key="room_new_name").set_value("Schlafzimer")
    _button(at, "Raum anlegen").click().run()
    _no_exception(at)
    text = all_text(at)
    assert "Ähnlicher Raum vorhanden" in text
    assert len(_repo(db).list_rooms()) == 1
    _button(at, "Trotzdem neu anlegen").click().run()
    _no_exception(at)
    assert sorted(r.name for r in _repo(db).list_rooms()) == ["Schlafzimer", "Schlafzimmer"]


def test_conflict_resolution_via_page(db):
    ids = seed(db, conflict=True)
    at = run_page("rooms")
    text = all_text(at)
    assert "Konflikte (1)" in [t.label for t in at.tabs]
    _button(at, "Neuen Wert übernehmen").click().run()
    _no_exception(at)
    repo = _repo(db)
    assert repo.list_conflicts(ids["sensor_id"]) == []
    resolved = repo.list_conflicts(ids["sensor_id"], unresolved_only=False)[0]
    assert resolved.resolution == ConflictResolution.USE_INCOMING
    df = repo.load_measurements([ids["sensor_id"]])
    row = df[df["ts"] == resolved.ts].iloc[0]
    assert row["humidity"] == pytest.approx(ids["incoming"]["humidity"])
    assert "Neuer Wert übernommen" in all_text(at) or text


def test_delete_room_requires_typed_name(db):
    ids = seed(db)
    at = run_page("rooms")
    delete = _button(at, "Raum endgültig löschen")
    assert delete.disabled
    assert "aktive Freigabelink" in all_text(at)
    at.text_input(key=f"room_del_confirm_{ids['room_id']}").set_value("Schlafzimmer").run()
    _button(at, "Raum endgültig löschen").click().run()
    _no_exception(at)
    assert _repo(db).list_rooms(include_archived=True) == []


def test_settings_validation_and_reset(db):
    at = run_page("settings")
    at.number_input(key="set_low_0").set_value(55.0)
    at.number_input(key="set_el_0").set_value(50.0)
    _button(at, "Einstellungen speichern").click().run()
    _no_exception(at)
    assert "Nicht gespeichert" in all_text(at)
    at.number_input(key="set_low_0").set_value(30.0)
    at.number_input(key="set_el_0").set_value(65.0)
    _button(at, "Einstellungen speichern").click().run()
    _no_exception(at)
    stored = _repo(db).get_settings()["analysis"]
    assert stored["humidity_elevated"] == 65.0 and stored["humidity_low"] == 30.0
    _button(at, "Standard wiederherstellen").click().run()
    _no_exception(at)
    assert _repo(db).get_settings()["analysis"]["humidity_elevated"] == 60.0
    assert "Lokal (SQLite" in all_text(at)


def test_login_form_in_supabase_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    at = run_page("login")
    _no_exception(at)
    assert at.text_input(key="login_password").proto.type == at.text_input(key="login_password").proto.PASSWORD
    text = all_text(at)
    assert "Administrator" in text
    assert "anon-test-key" not in text


@pytest.mark.parametrize("url_path", ["uebersicht", "analyse", "vergleich", "raeume", "einstellungen"])
def test_pages_reachable_via_app_navigation(db, url_path):
    """Über app.py (st.navigation): Seite per URL-Pfad öffnen; interne Links (st.page_link) funktionieren."""
    from streamlit.navigation.page import calc_hash
    from streamlit.testing.v1 import AppTest

    from tests.test_pages_helpers import ROOT

    seed(db)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    _no_exception(at)
    at._page_hash = calc_hash(url_path)  # AppTest.switch_page kennt nur dateibasierte Seiten
    at.run()
    _no_exception(at)
    assert len(at.get("page_link")) >= 8
    assert "Menüpunkt" not in all_text(at)  # kein Rückfall-Hinweis statt Link
