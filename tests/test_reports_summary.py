"""Tests für Zusammenfassung und Fazit (Laiensprache, nur datengestützte Aussagen)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from climate_dashboard.domain.models import AnalysisSettings, Room, Sensor
from climate_dashboard.reports.builder import ReportOptions, build_snapshot
from climate_dashboard.reports.summary import DEW_POINT_EXPLAINED, build_conclusion, build_summary
from tests.synthetic import make_measurements

ROOM = Room(id="r", name="Wohnzimmer")
SENSOR = Sensor(id="s", room_id="r", name="S")
FORBIDDEN = ("Schimmelbefall festgestellt", "Schimmel festgestellt", "Schimmel vorhanden", "Kondensation festgestellt",
             "ist Schimmel", "gefährlich", "dringend", "alarmierend", "weil ")


def _snap(start="2026-03-01", end="2026-03-29", **kw):
    df = make_measurements(start, end, 30, **kw)
    return build_snapshot(df, room=ROOM, sensors=[SENSOR], period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
                          period_end=datetime.fromisoformat(end).replace(tzinfo=UTC), options=ReportOptions(),
                          settings=AnalysisSettings(), expected_s=1800)


def _text(snap) -> str:
    c = snap["conclusion"]
    return " ".join(snap["summary"]) + " " + c["headline"] + " " + c["text"] + " " + " ".join(c["limitations"])


def test_dry_unremarkable_data_no_humidity_warning():
    s = _snap(base_rh=45)
    text = _text(s)
    assert s["conclusion"]["status"] == "unauffaellig"
    assert s["conclusion"]["level"] == "ok"
    assert "kamen nicht vor" in text or "nur selten" in text
    assert "erhöht" not in s["conclusion"]["text"]
    assert "Unsicherheit" not in text and "unsicher" not in text
    assert DEW_POINT_EXPLAINED in text  # Fachbegriff erklärt


def test_humid_data_mentions_share_and_phases():
    s = _snap(humid_spells=[("2026-03-02", "2026-03-12", 76)])
    text = _text(s)
    assert "der gemessenen Zeit lag sie bei 60" in text
    assert s["conclusion"]["status"] in ("feuchte", "schimmel")
    assert s["conclusion"]["level"] in ("hinweis", "auffaellig")
    assert "lässt sich nur vor Ort beurteilen" in text or "nicht ableiten" in text


def test_gaps_produce_uncertainty_sentence():
    s = _snap(gaps=[("2026-03-05", "2026-03-15")])
    assert s["quality"]["rating"] != "gut"
    assert any("Unsicherheit" in x for x in s["summary"])
    assert "unsicher" in s["conclusion"]["text"]


def test_no_forbidden_claims_and_limitations_always_named():
    for kw in ({}, {"humid_spells": [("2026-03-02", "2026-03-20", 85)]}, {"base_rh": 30}):
        s = _snap(**kw)
        text = _text(s)
        for word in FORBIDDEN:
            assert word not in text, (word, kw)
        assert "weder feststellen noch ausschließen" in text
        assert "Oberflächen" in text  # fehlende Oberflächentemperatur benannt
        assert 3 <= len(s["summary"]) <= 6


def test_numbers_german_format():
    s = _snap()
    text = " ".join(s["summary"])
    assert "°C" in text and "," in text
    assert "." not in text.split("Raumtemperatur lag im Mittel bei ")[1][:4]


def test_minimal_inputs():
    lines = build_summary(meta={"period_start": None, "period_end": None}, quality={"n": 0}, kpis=None,
                          humidity=None, condensation=None, assessment=None)
    assert lines and "zu wenige Messwerte" in lines[0]
    c = build_conclusion(assessment=None, quality=None)
    assert c["status"] == "keine_daten"
    json.dumps(c)
