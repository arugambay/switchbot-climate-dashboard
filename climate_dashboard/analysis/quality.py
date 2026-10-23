"""Datenqualität: Messdauern (Zeitgewichte), Datenlücken und Abdeckung.

Grundregel 4 (docs/ARCHITECTURE.md): Jede Messung repräsentiert die Zeit bis zur nächsten
Messung. Ist der Abstand größer als die Lückengrenze
``gap_threshold = max(gap_factor × erwartetes Intervall, min_gap_minutes)``, liegt eine
Datenlücke vor. Die Messung vor der Lücke repräsentiert dann nur ein *nominales* Intervall
(erwartetes Intervall bzw. – ohne Vorgabe – Median der regulären Abstände, höchstens die
Lückengrenze); der Rest des Abstands ist Lückenzeit und zählt weder als „auffällig“ noch als
„unauffällig“.

Dieses Modul enthält außerdem die gemeinsamen Hilfsfunktionen für die JSON-Serialisierung
der Ergebnis-Dataclasses (``jsonable``, ``ResultMixin``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import TS, AnalysisSettings

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")

# Bewertungsschwellen der Datenqualität (dokumentiert in docs/FORMELN.md)
RATING_GOOD_COVERAGE_PCT = 90.0  # ab dieser Zeitabdeckung „gut“
RATING_LIMITED_COVERAGE_PCT = 60.0  # ab dieser Zeitabdeckung „eingeschränkt“, darunter „unzureichend“
RATING_LONG_GAP_SHARE = 0.25  # längste Lücke > 25 % des Zeitraums → höchstens „eingeschränkt“
RATING_MIN_MEASUREMENTS = 2


# ---------------------------------------------------------------------------
# Serialisierung
# ---------------------------------------------------------------------------
def jsonable(obj: Any) -> Any:
    """Wandelt Ergebnisobjekte rekursiv in JSON-serialisierbare Werte um.

    Zeiten → ISO-8601 mit Offset, NaN/NaT/±inf → ``None``, numpy-Skalare → Python-Zahlen,
    Dataclasses → dict, DataFrames → Liste von Datensätzen.
    """
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, int | np.integer):
        return int(obj)
    if isinstance(obj, float | np.floating):
        value = float(obj)
        return value if math.isfinite(value) else None
    if obj is pd.NaT:
        return None
    if isinstance(obj, pd.Timestamp | datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return obj.total_seconds()
    if isinstance(obj, pd.DataFrame):
        return [jsonable(r) for r in obj.to_dict(orient="records")]
    if isinstance(obj, pd.Series):
        return [jsonable(v) for v in obj.tolist()]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [jsonable(v) for v in obj]
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


class ResultMixin:
    """Stellt ``to_dict()`` für Ergebnis-Dataclasses bereit."""

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self)


def to_seconds(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Zeitstempel (tz-aware) → Sekunden seit Epoche als float64 (auflösungsunabhängig)."""
    s = pd.Series(ts)
    if len(s) == 0:
        return np.empty(0, dtype=float)
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC")
    return (s - _EPOCH).dt.total_seconds().to_numpy(dtype=float)


def from_seconds(secs: float, tz: str | None = None) -> pd.Timestamp | None:
    if secs is None or not math.isfinite(secs):
        return None
    t = _EPOCH + pd.Timedelta(seconds=float(secs))
    return t.tz_convert(tz) if tz else t


def _ts(value: Any) -> pd.Timestamp | None:
    """Beliebige Zeitangabe → tz-aware UTC-Timestamp (naive Werte gelten als UTC)."""
    if value is None:
        return None
    t = pd.Timestamp(value)
    if t is pd.NaT:
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ---------------------------------------------------------------------------
# Intervalle und Messdauern
# ---------------------------------------------------------------------------
def gap_threshold_s(expected_s: float | None, settings: AnalysisSettings | None = None) -> float:
    """Lückengrenze in Sekunden: ``max(gap_factor × erwartetes Intervall, min_gap_minutes)``."""
    settings = settings or AnalysisSettings()
    floor = float(settings.min_gap_minutes) * 60.0
    if expected_s is None or not math.isfinite(expected_s) or expected_s <= 0:
        return floor
    return max(float(settings.gap_factor) * float(expected_s), floor)


def resolve_expected_interval(ts: pd.Series, expected_s: float | None = None) -> float | None:
    """Erwartetes Messintervall in Sekunden.

    Vorgabe (z. B. aus den Sensor-Einstellungen) hat Vorrang. Sonst robuste Erkennung:
    Median der positiven Abstände, ab einer Minute auf ganze Minuten gerundet. Der Median ist
    unempfindlich gegenüber einzelnen Ausreißern (Lücken, Doppelmessungen).
    """
    if expected_s is not None and math.isfinite(expected_s) and expected_s > 0:
        return float(expected_s)
    secs = np.sort(to_seconds(pd.Series(ts).dropna()))
    diffs = np.diff(secs)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return None
    med = float(np.median(diffs))
    return float(round(med / 60.0) * 60.0) if med >= 60 else med


def _nominal_interval(secs: np.ndarray, threshold_s: float) -> float:
    diffs = np.diff(secs)
    regular = diffs[(diffs > 0) & (diffs <= threshold_s)]
    if len(regular):
        return float(np.median(regular))
    return 0.0


def durations(
    ts: pd.Series,
    gap_threshold_s: float,
    *,
    nominal_s: float | None = None,
    period_end: Any = None,
) -> pd.Series:
    """Sekunden, die jede Messung repräsentiert (Zeitgewicht).

    - Abstand zur nächsten Messung, wenn ≤ Lückengrenze.
    - Bei einer Lücke (Abstand > Lückengrenze) und für die letzte Messung: nominales Intervall
      (``nominal_s`` bzw. Median der regulären Abstände), höchstens die Lückengrenze.
      Der übrige Abstand ist Lückenzeit.
    - Mit ``period_end`` wird kein Gewicht über das Periodenende hinaus vergeben.

    ``ts`` muss aufsteigend sortiert sein (doppelte Zeitstempel erhalten Gewicht 0).
    """
    ts = pd.Series(ts)
    secs = to_seconds(ts)
    n = len(secs)
    if n == 0:
        return pd.Series(np.empty(0), index=ts.index, dtype=float)
    nxt = np.full(n, np.nan)
    nxt[:-1] = np.diff(secs)
    if nominal_s is None:
        nominal_s = _nominal_interval(secs, gap_threshold_s)
    nominal = min(max(float(nominal_s), 0.0), float(gap_threshold_s))
    with np.errstate(invalid="ignore"):
        w = np.where(nxt <= gap_threshold_s, nxt, nominal)
    w = np.clip(w, 0.0, None)
    end = _ts(period_end)
    if end is not None:
        end_s = (end - _EPOCH).total_seconds()
        w = np.minimum(w, np.clip(end_s - secs, 0.0, None))
    return pd.Series(w, index=ts.index, dtype=float)


def gap_flags(ts: pd.Series, gap_threshold_s: float) -> np.ndarray:
    """True für Messungen, nach denen eine Datenlücke beginnt (nicht für die letzte Messung)."""
    secs = to_seconds(ts)
    flags = np.zeros(len(secs), dtype=bool)
    if len(secs) > 1:
        flags[:-1] = np.diff(secs) > gap_threshold_s
    return flags


# ---------------------------------------------------------------------------
# Qualitätsbericht
# ---------------------------------------------------------------------------
@dataclass
class Gap(ResultMixin):
    start: pd.Timestamp
    end: pd.Timestamp
    duration_s: float
    position: str  # 'start' (Periodenanfang) | 'inside' | 'end' (Periodenende) | 'all' (keine Daten)


@dataclass
class QualityReport(ResultMixin):
    n: int
    period_start: pd.Timestamp | None
    period_end: pd.Timestamp | None
    period_s: float
    first_ts: pd.Timestamp | None
    last_ts: pd.Timestamp | None
    expected_interval_s: float | None
    expected_interval_source: str  # 'vorgegeben' | 'erkannt' | 'unbekannt'
    observed_median_interval_s: float | None
    observed_intervals: dict[str, float]  # häufigste Abstände (Minuten → % der Abstände)
    gap_threshold_s: float
    covered_s: float  # durch Messungen repräsentierte Zeit
    uncovered_s: float  # Zeitraum − covered_s (Lückenzeit inkl. Rand)
    time_coverage_pct: float | None
    completeness_pct: float | None
    gaps: list[Gap] = field(default_factory=list)
    gap_count: int = 0
    gap_total_s: float = 0.0
    longest_gap_s: float = 0.0
    longest_gap: Gap | None = None
    rating: str = "unzureichend"  # 'gut' | 'eingeschränkt' | 'unzureichend'
    rating_reasons: list[str] = field(default_factory=list)


def _rate(n: int, coverage: float | None, longest_gap_s: float, period_s: float) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if n < RATING_MIN_MEASUREMENTS or coverage is None:
        return "unzureichend", ["Zu wenige Messungen im Zeitraum."]
    if coverage >= RATING_GOOD_COVERAGE_PCT:
        rating = "gut"
    elif coverage >= RATING_LIMITED_COVERAGE_PCT:
        rating = "eingeschränkt"
        reasons.append(
            f"Zeitabdeckung {coverage:.0f} % (unter {RATING_GOOD_COVERAGE_PCT:.0f} %)."
        )
    else:
        rating = "unzureichend"
        reasons.append(
            f"Zeitabdeckung {coverage:.0f} % (unter {RATING_LIMITED_COVERAGE_PCT:.0f} %)."
        )
    if period_s > 0 and longest_gap_s > RATING_LONG_GAP_SHARE * period_s and rating == "gut":
        rating = "eingeschränkt"
        reasons.append("Eine einzelne Lücke umfasst mehr als ein Viertel des Zeitraums.")
    return rating, reasons


def assess_quality(
    df: pd.DataFrame | None,
    start: Any = None,
    end: Any = None,
    expected_s: float | None = None,
    settings: AnalysisSettings | None = None,
) -> QualityReport:
    """Datenqualität eines Zeitraums ``[start, end)``.

    Ohne ``start``/``end`` gilt der Datenzeitraum (erste Messung bis letzte Messung + nominales
    Intervall). Lücken am Anfang/Ende des angefragten Zeitraums werden mitgezählt.
    """
    settings = settings or AnalysisSettings()
    tz = settings.timezone
    if df is None or TS not in getattr(df, "columns", []):
        ts = pd.Series(pd.DatetimeIndex([], tz="UTC"))
    else:
        ts = pd.Series(df[TS]).dropna()
        if len(ts) and ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        ts = ts.sort_values().reset_index(drop=True)
    start_t, end_t = _ts(start), _ts(end)

    # Erwartetes Intervall und Zeitgewichte auf dem vollständigen Datensatz (Sensoreigenschaft;
    # die letzte Messung vor ``end`` repräsentiert die Zeit bis zur nächsten Messung).
    given = expected_s is not None and expected_s > 0
    expected = resolve_expected_interval(ts, expected_s)
    source = "vorgegeben" if given else ("erkannt" if expected else "unbekannt")
    thr = gap_threshold_s(expected, settings)
    secs_all = to_seconds(ts)
    nominal = min(expected if expected else _nominal_interval(secs_all, thr), thr)
    w_all = durations(ts, thr, nominal_s=nominal).to_numpy() if len(ts) else np.empty(0)

    mask = np.ones(len(ts), dtype=bool)
    if start_t is not None:
        mask &= (ts >= start_t).to_numpy()
    if end_t is not None:
        mask &= (ts < end_t).to_numpy()
    ts = ts[mask].reset_index(drop=True)
    w = w_all[mask]
    n = len(ts)
    secs = secs_all[mask]

    if start_t is None:
        start_t = ts.iloc[0] if n else None
    if end_t is None:
        end_t = (ts.iloc[-1] + pd.Timedelta(seconds=nominal)) if n else None
    period_s = (end_t - start_t).total_seconds() if (start_t is not None and end_t is not None) else 0.0
    period_s = max(period_s, 0.0)
    if n and end_t is not None:
        end_s = (end_t - _EPOCH).total_seconds()
        w = np.minimum(w, np.clip(end_s - secs, 0.0, None))

    covered = float(w.sum())
    coverage = min(100.0, covered / period_s * 100.0) if period_s > 0 else None
    if n and period_s == 0:
        coverage = None
    completeness = None
    if expected and period_s > 0:
        completeness = min(100.0, n / (period_s / expected) * 100.0)

    def loc(t: pd.Timestamp) -> pd.Timestamp:
        return t.tz_convert(tz)

    gaps: list[Gap] = []
    if n == 0 and start_t is not None and end_t is not None and period_s > 0:
        gaps.append(Gap(loc(start_t), loc(end_t), period_s, "all"))
    elif n:
        lead = (ts.iloc[0] - start_t).total_seconds()
        if lead > thr:
            gaps.append(Gap(loc(start_t), loc(ts.iloc[0]), lead, "start"))
        diffs = np.diff(secs)
        for i in np.flatnonzero(diffs > thr):
            gaps.append(Gap(loc(ts.iloc[i]), loc(ts.iloc[i + 1]), float(diffs[i]), "inside"))
        tail = (end_t - ts.iloc[-1]).total_seconds()
        if tail > thr:
            gaps.append(Gap(loc(ts.iloc[-1]), loc(end_t), tail, "end"))

    longest = max(gaps, key=lambda g: g.duration_s) if gaps else None
    diffs_all = np.diff(secs)
    pos = diffs_all[diffs_all > 0]
    observed = float(np.median(pos)) if len(pos) else None
    mix: dict[str, float] = {}
    if len(pos):
        minutes = pd.Series(np.round(pos / 60.0, 1))
        counts = minutes.value_counts(normalize=True).head(6) * 100.0
        mix = {f"{k:g}": float(v) for k, v in counts.items()}

    rating, reasons = _rate(n, coverage, longest.duration_s if longest else 0.0, period_s)
    return QualityReport(
        n=n,
        period_start=loc(start_t) if start_t is not None else None,
        period_end=loc(end_t) if end_t is not None else None,
        period_s=period_s,
        first_ts=loc(ts.iloc[0]) if n else None,
        last_ts=loc(ts.iloc[-1]) if n else None,
        expected_interval_s=expected,
        expected_interval_source=source,
        observed_median_interval_s=observed,
        observed_intervals=mix,
        gap_threshold_s=thr,
        covered_s=covered,
        uncovered_s=max(period_s - covered, 0.0),
        time_coverage_pct=coverage,
        completeness_pct=completeness,
        gaps=gaps,
        gap_count=len(gaps),
        gap_total_s=float(sum(g.duration_s for g in gaps)),
        longest_gap_s=longest.duration_s if longest else 0.0,
        longest_gap=longest,
        rating=rating,
        rating_reasons=reasons,
    )
