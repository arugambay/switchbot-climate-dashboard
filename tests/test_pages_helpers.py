"""Gemeinsame Hilfen für die Seiten-Tests (nur synthetische Daten, nie data/private)."""

from __future__ import annotations

from pathlib import Path

from climate_dashboard.domain.models import ImportRecord, ImportStatus, MeasurementConflict, new_id
from tests.synthetic import make_measurements

ROOT = Path(__file__).resolve().parent.parent

# Etwa 8 Monate mit Lücken und feuchten Phasen; das Ende liegt bewusst in der Vergangenheit.
LONG_START = "2026-02-20 00:00"
LONG_END = "2026-10-20 00:00"
GAPS = [("2026-04-10 06:00", "2026-04-13 18:00"), ("2026-08-01 00:00", "2026-08-02 12:00")]
SPELLS = [
    ("2026-09-02 20:00", "2026-09-03 08:00", 74.0),
    ("2026-10-05 21:00", "2026-10-06 07:00", 72.0),
    ("2026-10-12 22:00", "2026-10-13 06:00", 66.0),
    ("2026-10-15 07:00", "2026-10-15 08:00", 71.0),
]


def seed(path: Path, *, long: bool = False, second_room: bool = False, conflict: bool = False) -> dict:
    """Legt in einer SQLite-Datei Raum/Sensor/Messungen an und gibt die IDs zurück."""
    from climate_dashboard.storage.sqlite_repo import SqliteRepository

    repo = SqliteRepository(str(path))
    room = repo.create_room("Schlafzimmer", "Nordseite")
    sensor = repo.create_sensor(room.id, "Hygrometer 1", expected_interval_s=1800)
    if long:
        df = make_measurements(LONG_START, LONG_END, 30, gaps=GAPS, humid_spells=SPELLS, drop_prob=0.01)
    else:
        df = make_measurements(
            "2026-09-10 00:00", "2026-10-20 00:00", 30,
            gaps=[("2026-10-01 00:00", "2026-10-01 12:00")], humid_spells=SPELLS,
        )  # fmt: skip
    record = ImportRecord(
        id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="Schlafzimmer_export.csv",
        file_sha256="0" * 64, status=ImportStatus.COMPLETED, rows_read=len(df), rows_new=len(df),
    )  # fmt: skip
    repo.create_import(record)
    repo.insert_measurements(sensor.id, record.id, df)
    out = {"room_id": room.id, "sensor_id": sensor.id, "import_id": record.id, "n": len(df), "df": df}
    if conflict:
        ts = df["ts"].iloc[10].to_pydatetime()
        existing = {m: float(df[m].iloc[10]) for m in ("temperature", "humidity")}
        incoming = {"temperature": existing["temperature"] + 2.0, "humidity": existing["humidity"] + 5.0}
        c = MeasurementConflict(new_id(), sensor.id, record.id, ts, existing, incoming)
        repo.add_conflicts([c])
        out["conflict_id"] = c.id
        out["incoming"] = incoming
    if second_room:
        room2 = repo.create_room("Bad")
        s2 = repo.create_sensor(room2.id, "Bad Sensor", expected_interval_s=1800)
        df2 = make_measurements("2026-10-01 00:00", "2026-10-20 00:00", 30, base_rh=62.0, seed=3)
        rec2 = ImportRecord(new_id(), s2.id, room2.id, "Bad.csv", "1" * 64, status=ImportStatus.COMPLETED)
        repo.create_import(rec2)
        repo.insert_measurements(s2.id, rec2.id, df2)
        out["room2_id"] = room2.id
        out["sensor2_id"] = s2.id
    repo.close()
    return out


def local_db(tmp_path, monkeypatch):
    """Generator für eine Fixture: lokaler Modus mit eigener SQLite-Datei je Test."""
    from climate_dashboard.storage import factory

    path = tmp_path / "pages.sqlite"
    monkeypatch.setenv("SQLITE_PATH", str(path))
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.chdir(ROOT)
    factory.clear_cache()
    yield path
    factory.clear_cache()


def page_script(module: str) -> str:
    """Quelltext für ``AppTest.from_string``: rendert eine Seite im Designsystem."""
    return (
        "from climate_dashboard.ui import theme\n"
        f"from climate_dashboard.ui.pages import {module} as page\n"
        "theme.apply_plotly_template()\n"
        "theme.inject_css('app')\n"
        "page.render()\n"
    )


def run_page(module: str, timeout: int = 120):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_string(page_script(module), default_timeout=timeout).run()


def all_text(at) -> str:
    parts = [el.proto.body for el in at.get("html")]
    parts += [str(getattr(el, "value", "")) for el in at.markdown]
    parts += [str(el.value) for el in at.caption]
    parts += [str(el.value) for el in at.error] + [str(el.value) for el in at.warning]
    parts += [str(el.value) for el in at.info] + [str(el.value) for el in at.success]
    parts += [str(el.label) for el in at.expander] + [str(el.label) for el in at.tabs]
    return "\n".join(parts)
