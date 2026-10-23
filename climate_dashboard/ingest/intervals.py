"""Messintervalle erkennen, Lücken und Datenabdeckung bestimmen.

Zeitstempel werden hier ausschließlich gelesen und nie verändert.

Lückengrenze, Zeitabdeckung und Messvollständigkeit werden NICHT hier definiert, sondern
über ``analysis.quality`` (``gap_threshold_s``, ``assess_quality``) berechnet – damit Import
und Analyse dieselben Zahlen zeigen (siehe docs/ARCHITECTURE.md, Regeln 4 und 5):

* Lückengrenze = ``max(gap_factor × erwartetes Intervall, min_gap_minutes)``.
* Zeitraum = erste Messung bis letzte Messung + erwartetes Intervall.
* *Zeitabdeckung* = durch Messungen repräsentierte Zeit ÷ Zeitraum.
* *Messvollständigkeit* = Anzahl Messungen ÷ (Zeitraum ÷ erwartetes Intervall), max. 100 %.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis.quality import assess_quality
from climate_dashboard.domain.models import TS, AnalysisSettings

# Auswahl im Import-Assistenten (Reihenfolge = Anzeige). Benutzerdefiniert: separat in der UI.
INTERVAL_PRESETS: dict[str, int | None] = {
    "Automatisch erkennen": None,
    "1 Minute": 60,
    "15 Minuten": 900,
    "30 Minuten": 1800,
    "60 Minuten": 3600,
    "12 Stunden": 43200,
    "24 Stunden": 86400,
}

# Typische Messintervalle (s), auf die erkannte Werte „einrasten“
TYPICAL_INTERVALS_S: tuple[int, ...] = (
    60,
    120,
    300,
    600,
    900,
    1200,
    1800,
    3600,
    7200,
    10800,
    21600,
    43200,
    86400,
)
_SNAP_TOLERANCE = 0.1  # ±10 %
IRREGULAR_TOLERANCE = 0.1  # Abstand weicht um mehr als 10 % vom erwarteten Intervall ab


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).isoformat()


def format_duration_de(seconds: float | None) -> str:
    """Dauer in deutscher Kurzform: „30 Minuten“, „1 Stunde 30 Minuten“, „2 Tage 3 Stunden“."""
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return "–"
    s = int(round(seconds))

    def unit(value: int, singular: str, plural: str) -> str:
        return f"{value} {singular if value == 1 else plural}"

    if s < 60:
        return unit(s, "Sekunde", "Sekunden")
    if s < 3600:
        return unit(round(s / 60), "Minute", "Minuten")
    if s < 86400:
        hours, minutes = divmod(round(s / 60), 60)
        text = unit(hours, "Stunde", "Stunden")
        return text + (" " + unit(minutes, "Minute", "Minuten") if minutes else "")
    days, rest = divmod(s, 86400)
    hours = round(rest / 3600)
    text = unit(days, "Tag", "Tage")
    return text + (" " + unit(hours, "Stunde", "Stunden") if hours else "")


def snap_interval(seconds: float) -> int:
    """Rastet einen Abstand auf ein typisches Intervall ein (±10 %), sonst auf volle Minuten
    (bzw. Sekunden unter einer Minute)."""
    for typical in TYPICAL_INTERVALS_S:
        if abs(seconds - typical) <= _SNAP_TOLERANCE * typical:
            return typical
    if seconds >= 60:
        return int(round(seconds / 60) * 60)
    return max(int(round(seconds)), 1)


def _sorted_unique_ns(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Sortierte, eindeutige Zeitpunkte als int64-Nanosekunden (UTC; naive Werte gelten als UTC)."""
    s = pd.Series(ts).dropna()
    if s.empty:
        return np.array([], dtype="int64")
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return np.unique(s.astype("datetime64[ns]").to_numpy().astype("int64"))


def _diffs_s(ns: np.ndarray) -> np.ndarray:
    return np.diff(ns) / 1e9


def detect_interval(ts: pd.Series | pd.DatetimeIndex) -> int | None:
    """Erkennt das typische Messintervall in Sekunden (``None`` bei < 2 Messungen).

    Vorgehen: Abstände auf typische Intervalle bzw. volle Minuten runden, häufigsten Wert
    (Modus) bestimmen. Ist der Modus wenig dominant (< 30 % der Abstände), wird stattdessen
    der eingerastete Median verwendet – das ist bei gemischten Intervallen robuster.
    """
    diffs = _diffs_s(_sorted_unique_ns(ts))
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return None
    uniq, inverse = np.unique(diffs, return_inverse=True)
    rounded = np.array([snap_interval(float(d)) for d in uniq], dtype="int64")[inverse]
    values, counts = np.unique(rounded, return_counts=True)
    order = np.lexsort((values, -counts))  # häufigster zuerst, bei Gleichstand kleinerer Wert
    mode, share = int(values[order[0]]), counts[order[0]] / len(rounded)
    if share >= 0.3:
        return mode
    return snap_interval(float(np.median(diffs)))


@dataclass
class Gap:
    start: pd.Timestamp  # letzte Messung vor der Lücke
    end: pd.Timestamp  # erste Messung nach der Lücke
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"start": _iso(self.start), "end": _iso(self.end), "duration_s": float(self.duration_s)}


@dataclass
class IntervalSegment:
    """Abschnitt mit (dauerhaft) gleichem typischem Intervall."""

    start: pd.Timestamp
    end: pd.Timestamp
    interval_s: int

    def to_dict(self) -> dict[str, Any]:
        return {"start": _iso(self.start), "end": _iso(self.end), "interval_s": int(self.interval_s)}


@dataclass
class IntervalReport:
    expected_s: int | None
    expected_source: str | None  # 'user' | 'auto' | None (zu wenige Daten)
    detected_s: int | None
    observed_median_s: float | None
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    n: int
    gap_threshold_s: float | None
    expected_count: int | None
    completeness_pct: float | None
    time_coverage_pct: float | None
    gaps: list[Gap] = field(default_factory=list)
    longest_gap: Gap | None = None
    max_spacing_s: float | None = None
    share_irregular_pct: float | None = None
    interval_changes: list[IntervalSegment] = field(default_factory=list)
    deviates_from_expected: bool = False
    deviation_text: str | None = None

    @property
    def gap_total_s(self) -> float:
        return float(sum(g.duration_s for g in self.gaps))

    def to_dict(self) -> dict[str, Any]:
        def f(x: float | None) -> float | None:
            return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)

        return {
            "expected_s": self.expected_s,
            "expected_source": self.expected_source,
            "detected_s": self.detected_s,
            "observed_median_s": f(self.observed_median_s),
            "start": _iso(self.start),
            "end": _iso(self.end),
            "n": int(self.n),
            "gap_threshold_s": f(self.gap_threshold_s),
            "expected_count": self.expected_count,
            "completeness_pct": f(self.completeness_pct),
            "time_coverage_pct": f(self.time_coverage_pct),
            "gap_count": len(self.gaps),
            "gap_total_s": self.gap_total_s,
            "gaps": [g.to_dict() for g in self.gaps],
            "longest_gap": self.longest_gap.to_dict() if self.longest_gap else None,
            "max_spacing_s": f(self.max_spacing_s),
            "share_irregular_pct": f(self.share_irregular_pct),
            "interval_changes": [s.to_dict() for s in self.interval_changes],
            "deviates_from_expected": bool(self.deviates_from_expected),
            "deviation_text": self.deviation_text,
        }


MIN_SEGMENT_BLOCKS = 2  # ein Abschnitt muss mindestens so viele Blöcke umfassen („dauerhaft“)


def _segments(ns: np.ndarray, diffs: np.ndarray, threshold: float, expected: int) -> list[IntervalSegment]:
    """Erkennt dauerhafte Intervallwechsel.

    Die Reihe wird in Blöcke (mind. 7 Tage bzw. 20 Intervalle) geteilt; je Block wird der
    eingerastete Median der Abstände (ohne Lücken) bestimmt. Gleiche Nachbarblöcke werden
    zusammengefasst; Abschnitte, die kürzer als ``MIN_SEGMENT_BLOCKS`` Blöcke sind, werden dem
    Vorgänger (bzw. Nachfolger) zugeschlagen (Intervall des längeren Abschnitts gilt). So erzeugen
    einzelne unruhige Wochen keine Scheinwechsel. Rückgabe leer, wenn es keinen Wechsel gibt.
    """
    block_ns = int(max(7 * 86400, 20 * expected) * 1e9)
    if len(ns) < 3 or ns[-1] - ns[0] < 2 * MIN_SEGMENT_BLOCKS * block_ns:
        return []

    def median_of(lo: int, hi: int) -> int | None:
        d = diffs[lo:hi]
        d = d[d <= threshold]
        return snap_interval(float(np.median(d))) if len(d) >= 3 else None

    block_id = (ns[:-1] - ns[0]) // block_ns
    bounds = np.flatnonzero(np.diff(block_id)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [len(diffs)]))
    # Segment = [lo, hi, Anzahl Blöcke, Median]; Diff-Indizes lo..hi-1
    segs: list[list[Any]] = []
    for lo, hi in zip(starts, ends, strict=True):
        med = median_of(int(lo), int(hi))
        if med is None:
            if segs:
                segs[-1][1] = int(hi)
            continue
        segs.append([int(lo), int(hi), 1, med])

    def merge_equal(items: list[list[Any]]) -> list[list[Any]]:
        out: list[list[Any]] = []
        for seg in items:
            if out and out[-1][3] == seg[3]:
                out[-1][1] = seg[1]
                out[-1][2] += seg[2]
            else:
                out.append(list(seg))
        return out

    segs = merge_equal(segs)
    for _ in range(20):
        short = [i for i, seg in enumerate(segs) if seg[2] < MIN_SEGMENT_BLOCKS]
        if len(segs) < 2 or not short:
            break
        i = short[0]
        j = i - 1 if i > 0 else i + 1
        lo, hi = min(segs[i][0], segs[j][0]), max(segs[i][1], segs[j][1])
        label = segs[i][3] if segs[i][2] > segs[j][2] else segs[j][3]  # längerer Abschnitt bestimmt
        merged = [lo, hi, segs[i][2] + segs[j][2], label]
        k = min(i, j)
        segs[k : k + 2] = [merged]
        segs = merge_equal(segs)
    if len(segs) < 2:
        return []

    def to_ts(v: int) -> pd.Timestamp:
        return pd.Timestamp(int(v), tz="UTC")

    return [IntervalSegment(to_ts(ns[lo]), to_ts(ns[hi]), int(m)) for lo, hi, _, m in segs]


def analyze_intervals(
    ts: pd.Series | pd.DatetimeIndex,
    expected_s: int | None = None,
    settings: AnalysisSettings | None = None,
) -> IntervalReport:
    """Analysiert Intervalle, Lücken und Abdeckung einer Zeitreihe (Duplikate zählen einfach).

    ``expected_s``: vom Nutzer angegebenes Intervall; ``None`` = automatisch erkennen.
    ``settings``: Lückenparameter (``gap_factor``, ``min_gap_minutes``); Standard
    ``AnalysisSettings()``. Abdeckungszahlen stammen aus ``analysis.quality.assess_quality``.
    """
    settings = settings or AnalysisSettings()
    ns = _sorted_unique_ns(ts)
    n = len(ns)
    detected = detect_interval(pd.Series(pd.to_datetime(ns, utc=True))) if n >= 2 else None
    source = "user" if expected_s else ("auto" if detected else None)
    expected = int(expected_s) if expected_s else detected
    start = pd.Timestamp(int(ns[0]), tz="UTC") if n else None
    end = pd.Timestamp(int(ns[-1]), tz="UTC") if n else None
    report = IntervalReport(
        expected_s=expected,
        expected_source=source,
        detected_s=detected,
        observed_median_s=None,
        start=start,
        end=end,
        n=n,
        gap_threshold_s=None,
        expected_count=None,
        completeness_pct=None,
        time_coverage_pct=None,
    )
    if n < 2 or not expected:
        if n == 1:
            report.expected_count = 1
            report.completeness_pct = 100.0
            report.time_coverage_pct = 100.0
        return report

    diffs = _diffs_s(ns)
    unique_ts = pd.DataFrame({TS: pd.to_datetime(ns, utc=True)})
    quality = assess_quality(unique_ts, expected_s=float(expected), settings=settings)
    threshold = float(quality.gap_threshold_s)
    report.gap_threshold_s = threshold
    report.observed_median_s = float(np.median(diffs))
    report.max_spacing_s = float(diffs.max())

    gap_idx = np.flatnonzero(diffs > threshold)
    report.gaps = [
        Gap(pd.Timestamp(int(ns[i]), tz="UTC"), pd.Timestamp(int(ns[i + 1]), tz="UTC"), float(diffs[i]))
        for i in gap_idx
    ]
    if report.gaps:
        report.longest_gap = max(report.gaps, key=lambda g: g.duration_s)
    report.time_coverage_pct = quality.time_coverage_pct
    report.completeness_pct = quality.completeness_pct
    report.expected_count = int(round(quality.period_s / expected))
    tol = max(IRREGULAR_TOLERANCE * expected, 5.0)
    report.share_irregular_pct = 100.0 * float(np.mean(np.abs(diffs - expected) > tol))
    report.interval_changes = _segments(ns, diffs, threshold, expected)

    if source == "user" and detected and abs(detected - expected) > _SNAP_TOLERANCE * expected:
        report.deviates_from_expected = True
        report.deviation_text = (
            f"Angegeben ist ein Messintervall von {format_duration_de(expected)}, in den Daten "
            f"überwiegt jedoch ein Abstand von {format_duration_de(detected)}."
        )
    return report
