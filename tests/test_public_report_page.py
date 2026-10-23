"""Öffentliche Berichtsansicht über ``app.py?r=<token>`` (streamlit AppTest)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from climate_dashboard.domain.models import AnalysisSettings, ImportRecord, new_id
from climate_dashboard.reports import sharing
from climate_dashboard.reports.builder import ReportOptions, build_snapshot, data_fingerprint
from climate_dashboard.storage import factory
from climate_dashboard.storage.sqlite_repo import SqliteRepository
from tests.synthetic import make_measurements

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
START = datetime(2026, 2, 1, tzinfo=UTC)
END = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    db = tmp_path / "public.sqlite"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "APP_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.chdir(ROOT)
    repo = SqliteRepository(db)
    room = repo.create_room("Wohnküche Äußere Ecke")
    sensor = repo.create_sensor(room.id, "Geheimer Sensorname", expected_interval_s=1800)
    imp = repo.create_import(ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="a.csv",
                                          file_sha256="c" * 64))
    repo.insert_measurements(sensor.id, imp.id, make_measurements("2026-02-01", "2026-03-01", 30,
                                                                  humid_spells=[("2026-02-10", "2026-02-11", 72)]))
    df = repo.load_measurements([sensor.id], START, END)
    snap = build_snapshot(df, room=room, sensors=[sensor], period_start=START, period_end=END,
                          options=ReportOptions(title="Klimabericht Februar"), settings=AnalysisSettings(),
                          expected_s=1800, version=1)
    rep = repo.create_report(room.id, "Klimabericht Februar")
    repo.add_report_version(rep.id, period_start=START, period_end=END, config={}, content=snap,
                            data_fingerprint=data_fingerprint(df, [sensor.id], START, END))
    token = sharing.generate_token()
    share = repo.create_share(rep.id, token_hash=sharing.hash_token(token), token_hint=sharing.token_hint(token))
    repo.close()
    factory.clear_cache()
    yield token, {"room": room.id, "sensor": sensor.id, "report": rep.id, "share": share.id}, db
    factory.clear_cache()


def _html(at: AppTest) -> str:
    return "\n".join(el.proto.body for el in at.get("html"))


def _run(token: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["r"] = token
    return at.run()


def test_valid_token_shows_report(shared):
    token, ids, _ = shared
    at = _run(token)
    assert not at.exception
    body = _html(at)
    assert "Klimabericht Februar" in body
    assert "Wohnküche Äußere Ecke" in body
    assert "Kurzfazit" in body and "Zusammenfassung" in body
    assert "schreibgeschützte Ansicht" in body
    assert len(at.get("plotly_chart")) >= 2
    assert len(at.get("download_button")) == 1
    # keine Navigation, keine Sidebar, keine internen Elemente
    assert len(at.get("page_link")) == 0
    assert len(at.sidebar.children) == 0
    assert len(at.get("file_uploader")) == 0
    assert "Lokaler Entwicklungsmodus" not in body
    assert "Import" not in [b.label for b in at.button]
    assert "Einstellungen" not in body
    # keine IDs, kein Token, kein Sensorname (nicht freigegeben)
    for secret in (token, *ids.values(), "Geheimer Sensorname"):
        assert secret not in body


def test_invalid_token_shows_neutral_page(shared):
    _, _, _ = shared
    for bad in ("abc", sharing.generate_token()):
        at = _run(bad)
        assert not at.exception
        body = _html(at)
        assert "nicht (mehr) verfügbar" in body
        assert "Wohnküche" not in body and bad not in body
        assert len(at.get("plotly_chart")) == 0


def test_revoked_token_shows_neutral_page(shared):
    token, ids, db = shared
    repo = SqliteRepository(db)
    repo.revoke_share(ids["share"])
    repo.close()
    factory.clear_cache()
    body = _html(_run(token))
    assert "nicht (mehr) verfügbar" in body
    assert "Klimabericht Februar" not in body


def _reports_page():
    from climate_dashboard.ui import theme
    from climate_dashboard.ui.pages import reports

    theme.apply_plotly_template()
    theme.inject_css("app")
    reports.render()


def test_internal_reports_page_create_save_share(shared):
    token, ids, db = shared
    at = AppTest.from_function(_reports_page, default_timeout=180).run()
    assert not at.exception
    build = next(b for b in at.button if b.label.startswith("4 · Bericht erstellen"))
    build.click().run()
    assert not at.exception
    assert "Vorschau" in _html(at)
    save = next(b for b in at.button if b.label.startswith("5 · Bericht speichern"))
    save.click().run()
    assert not at.exception
    repo = SqliteRepository(db)
    try:
        assert len(repo.list_reports()) == 2
    finally:
        repo.close()
    share_btn = [b for b in at.button if b.label.startswith("6 · Freigabelink erzeugen")]
    assert share_btn
    share_btn[0].click().run()
    assert not at.exception
    codes = [c.value for c in at.code]
    assert any("/?r=" in c for c in codes)
    assert "nur jetzt" in _html(at)
