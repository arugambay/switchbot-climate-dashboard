"""Tests für den PDF-Export (fpdf2 + matplotlib, ohne Browser)."""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime

import pytest

from climate_dashboard.domain.models import AnalysisSettings, Room, Sensor
from climate_dashboard.reports.builder import ReportOptions, build_snapshot
from climate_dashboard.reports.pdf import render_pdf
from tests.synthetic import make_measurements

LONG_NAME = "Schlafzimmer Südwest (Außenwand, Fensterseite Straße) – Übergangsbereich Flur/Küche " * 3


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def _text_streams(pdf: bytes) -> bytes:
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            out += zlib.decompress(m.group(1))
        except zlib.error:
            pass
    return out


@pytest.fixture(scope="module")
def snapshot():
    df = make_measurements("2025-12-10", "2026-03-05", 30, humid_spells=[("2026-01-10", "2026-01-13", 76)],
                           gaps=[("2026-02-01", "2026-02-04")])
    return build_snapshot(df, room=Room(id="r", name=LONG_NAME), sensors=[Sensor(id="s", room_id="r", name="Ä-Sensor")],
                          period_start=datetime(2025, 12, 10, tzinfo=UTC), period_end=datetime(2026, 3, 5, tzinfo=UTC),
                          options=ReportOptions(title="Klimabericht Wohnung Größe", note="Sensor hängt über der Tür.",
                                                show_sensor_info=True, surface_offset_k=4),
                          settings=AnalysisSettings(), expected_s=1800, version=2)


def test_pdf_is_valid_multipage(snapshot):
    pdf = render_pdf(snapshot)
    assert pdf.startswith(b"%PDF")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert _page_count(pdf) >= 4


def test_pdf_embeds_unicode_font_and_page_numbers(snapshot):
    pdf = render_pdf(snapshot)
    assert b"DejaVu" in pdf
    # Fußzeile mit Seitenzahl: der Alias {nb} muss ersetzt sein
    assert b"{nb}" not in _text_streams(pdf)


def test_long_umlaut_room_name_renders(snapshot):
    snap = dict(snapshot)
    snap["meta"] = dict(snapshot["meta"], room_name="Ä" * 300, title="Ü" * 200)
    assert render_pdf(snap).startswith(b"%PDF")


@pytest.mark.parametrize("snap", [{}, None, {"meta": {"title": "Nur Titel"}},
                                  {"meta": {"room_name": "Küche"}, "summary": ["Ein Satz."],
                                   "conclusion": {"level": "ok", "headline": "Ok", "text": "Text"}}])
def test_minimal_snapshot_renders(snap):
    pdf = render_pdf(snap)
    assert pdf.startswith(b"%PDF") and _page_count(pdf) >= 1


def test_empty_data_snapshot_renders():
    snap = build_snapshot(None, room=Room(id="r", name="Leer"), sensors=[], period_start=datetime(2026, 1, 1, tzinfo=UTC),
                          period_end=datetime(2026, 2, 1, tzinfo=UTC), options=ReportOptions(),
                          settings=AnalysisSettings(), expected_s=None)
    assert render_pdf(snap).startswith(b"%PDF")
