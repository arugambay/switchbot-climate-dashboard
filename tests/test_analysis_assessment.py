import json

import numpy as np
import pandas as pd

from climate_dashboard.analysis import assessment
from climate_dashboard.analysis.assessment import LIMITATION_MOULD, LIMITATION_OUTDOOR
from climate_dashboard.analysis.condensation import NO_SURFACE_NOTE
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings
from tests.synthetic import make_measurements

TZ = "Europe/Berlin"


def utc_series(start_local, end_local, freq="30min", rh=50.0, t=20.0, drop=None) -> pd.DataFrame:
    s = pd.Timestamp(start_local, tz=TZ).tz_convert("UTC")
    e = pd.Timestamp(end_local, tz=TZ).tz_convert("UTC")
    ts = pd.date_range(s, e, freq=freq, inclusive="left")
    if drop:
        for a, b in drop:
            ts = ts[(ts < pd.Timestamp(a, tz=TZ)) | (ts >= pd.Timestamp(b, tz=TZ))]
    rh_v = rh(ts) if callable(rh) else rh
    return pd.DataFrame({TS: ts, TEMPERATURE: t, HUMIDITY: rh_v})


def all_text(a: assessment.Assessment) -> str:
    return json.dumps(a.to_dict(), ensure_ascii=False)


def test_dry_unremarkable_data_has_no_humidity_warning():
    df = make_measurements("2026-03-02", "2026-03-16", 10, base_rh=45.0, base_t=21.0)
    a = assessment.assess(df, AnalysisSettings())
    assert a.overall.key == "unauffaellig"
    assert not a.overall.uncertain
    assert all(f.level == "ok" for f in a.findings if f.category in ("feuchte", "dauer", "taupunkt"))
    assert not a.by_category("kondensation")
    assert "wiederholt erhöht" not in all_text(a)
    assert LIMITATION_MOULD in a.limitations and LIMITATION_OUTDOOR in a.limitations
    assert a.ventilation_hints == []
    json.dumps(a.to_dict())


def test_sustained_high_humidity_on_several_days():
    # jede Nacht 22–6 Uhr 75 % rF, sonst 55 %
    def rh(ts):
        return np.where((ts.tz_convert(TZ).hour >= 22) | (ts.tz_convert(TZ).hour < 6), 75.0, 55.0)
    df = utc_series("2026-03-02", "2026-03-09", rh=rh)
    a = assessment.assess(df, AnalysisSettings())
    assert a.overall.key == "schimmel"
    assert a.basis["mould_days_room"] >= 3
    feuchte = a.by_category("feuchte")[0]
    assert feuchte.level == "auffaellig"
    assert feuchte.text.startswith("Die Luftfeuchtigkeit war in diesem Zeitraum wiederholt erhöht.")
    assert a.by_category("dauer")[0].text.startswith("Die erhöhten Werte hielten über mehrere Stunden an.")
    assert a.by_category("kondensation")[0].text == NO_SURFACE_NOTE
    assert any("nachts" in h.observation for h in a.ventilation_hints)
    assert all(h.unknown for h in a.ventilation_hints)


def test_elevated_but_not_high_is_humidity_status():
    df = utc_series("2026-03-02", "2026-03-09", rh=65.0)
    a = assessment.assess(df, AnalysisSettings())
    assert a.overall.key == "feuchte"
    assert a.overall.label == "Möglicherweise kritische Feuchtebedingungen"


def test_surface_assumption_changes_condensation_statement():
    df = utc_series("2026-03-02 00:00", "2026-03-03 12:00", rh=60.0)  # Td ≈ 12 °C
    plain = assessment.assess(df, AnalysisSettings())
    assert plain.overall.key != "kondensation"
    assert not any(f.category == "kondensation" and f.level == "auffaellig" for f in plain.findings)
    cold = assessment.assess(df, AnalysisSettings(surface_offset_k=9.0))
    assert cold.overall.key == "kondensation"
    cond = cold.by_category("kondensation")[0]
    assert cond.level == "auffaellig" and "Annahme" in cond.text
    assert any("ANNAHME" in x for x in cold.limitations)
    mild = assessment.assess(df, AnalysisSettings(surface_offset_k=2.0))
    assert mild.overall.key != "kondensation"


def test_low_spread_finding():
    df = utc_series("2026-03-02", "2026-03-04", rh=87.0)
    a = assessment.assess(df, AnalysisSettings())
    t = a.by_category("taupunkt")
    assert t and t[0].text.startswith("Der geringe Abstand zum Taupunkt könnte")


def test_data_gaps_make_assessment_uncertain():
    df = utc_series("2026-03-02", "2026-03-09", drop=[("2026-03-04", "2026-03-06")])
    a = assessment.assess(df, AnalysisSettings())
    dq = a.by_category("datenqualitaet")
    assert dq and dq[0].text == "Wegen längerer Messlücken ist die Bewertung dieses Zeitraums unsicher."
    assert a.overall.uncertain


def test_empty_data():
    a = assessment.assess(utc_series("2026-03-02", "2026-03-02"))
    assert a.overall.key == "keine_daten"
    json.dumps(a.to_dict())
    one = assessment.assess(utc_series("2026-03-02 10:00", "2026-03-02 10:30"))
    assert one.overall.key == "keine_daten"


def test_change_after_describes_without_causality():
    def rh(ts):
        return np.where(ts < pd.Timestamp("2026-03-09", tz=TZ), 60.0, 50.0)
    df = utc_series("2026-03-02", "2026-03-16", rh=rh)
    ch = assessment.change_after(df, pd.Timestamp("2026-03-09", tz=TZ), AnalysisSettings())
    assert ch.window_s == 7 * 86400
    rh_f = [f for f in ch.findings if "Luftfeuchtigkeit" in f.text][0]
    assert "um 10 Prozentpunkte niedriger" in rh_f.text
    assert rh_f.evidence["delta"] == -10
    text = " ".join(f.text for f in ch.findings)
    assert "weil" not in text and "wegen" not in text
    assert any("nicht ableiten" in c for c in ch.caveats)
    json.dumps(ch.to_dict())
    none = assessment.change_after(df, pd.Timestamp("2027-01-01", tz=TZ), AnalysisSettings())
    assert none.comparison is None


def test_scattered_humid_days_are_not_mould_status():
    # einzelne feuchte Nächte im Abstand von 10 Tagen: kein „anhaltend über längere Dauer“
    nights = [pd.Timestamp(d, tz=TZ) for d in ("2026-03-03 22:00", "2026-03-13 22:00", "2026-03-23 22:00")]

    def rh(ts):
        out = np.full(len(ts), 50.0)
        for n in nights:
            out[(ts >= n) & (ts < n + pd.Timedelta(hours=8))] = 75.0
        return out

    a = assessment.assess(utc_series("2026-03-02", "2026-03-30", rh=rh), AnalysisSettings())
    assert a.basis["mould_days_room"] == 3
    assert a.basis["mould_days_room_max_7d"] == 1
    assert a.overall.key != "schimmel"
