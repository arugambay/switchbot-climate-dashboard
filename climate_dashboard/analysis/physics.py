"""Feuchte-Physik: Sättigungsdampfdruck, Taupunkt, absolute Feuchte, VPD.

Magnus-Formel über Wasser mit den Koeffizienten nach Alduchov & Eskridge (1996):
``e_s(T) = 0,6112 kPa · exp(17,62 · T / (243,12 + T))``.

Gültigkeitsbereich: ca. −45 °C … 60 °C (Fehler < 0,4 % gegenüber genaueren Formeln). Außerhalb
liefern die Funktionen NaN. Relative Feuchte ≤ 0 % oder > 100 % ist physikalisch nicht sinnvoll
für Raumluft und ergibt ebenfalls NaN. Unter 0 °C wird bewusst „über Wasser“ gerechnet (wie
übliche Raumklima-Sensoren); für Eisoberflächen wäre eine andere Koeffizientenwahl nötig.

Alle Funktionen sind vektorisiert (Skalare, numpy-Arrays, pandas-Series). Series-Eingaben
liefern Series mit gleichem Index. Details und Herleitung: docs/FORMELN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis.quality import ResultMixin
from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    METRICS,
    TEMPERATURE,
    TS,
    VPD,
)

MAGNUS_A = 17.62
MAGNUS_B = 243.12  # °C
MAGNUS_E0_KPA = 0.6112  # kPa (= 6,112 hPa)
WATER_VAPOR_FACTOR = 216.7  # g·K/(m³·hPa) = 1000 / R_v (R_v = 461,5 J/(kg·K)) · 100 Pa/hPa
KELVIN = 273.15

T_MIN_C = -45.0
T_MAX_C = 60.0

CALC_PREFIX = "calc_"
DERIVED: tuple[str, ...] = (DEW_POINT, VPD, ABS_HUMIDITY)

# Toleranzen für den Plausibilitätsvergleich importiert ↔ berechnet (Begründung: docs/FORMELN.md).
# Eingangswerte sind gerundet (T auf 0,1 °C, rF auf 1 %); die importierten Zielwerte ebenfalls.
DEFAULT_TOLERANCES: dict[str, float] = {DEW_POINT: 0.5, VPD: 0.05, ABS_HUMIDITY: 0.3}
PLAUSIBILITY_MAX_EXCEED_PCT = 1.0  # bis zu 1 % Abweichungen gelten als unauffällig


def _arr(x: Any) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _wrap(values: np.ndarray, like: Any) -> Any:
    if isinstance(like, pd.Series):
        return pd.Series(values, index=like.index, dtype=float)
    if np.ndim(like) == 0:
        return float(values)
    return values


def _like(*args: Any) -> Any:
    """Bestimmt die Ausgabeform: erste Series, sonst erstes Array, sonst Skalar."""
    for a in args:
        if isinstance(a, pd.Series):
            return a
    for a in args:
        if np.ndim(a) > 0:
            return a
    return args[0]


def _valid_t(t: np.ndarray) -> np.ndarray:
    return (t >= T_MIN_C) & (t <= T_MAX_C)


def _valid_rh(rh: np.ndarray) -> np.ndarray:
    return (rh > 0) & (rh <= 100)


def saturation_vapor_pressure_kpa(t: Any) -> Any:
    """Sättigungsdampfdruck über Wasser in kPa für Lufttemperatur ``t`` in °C."""
    ta = _arr(t)
    with np.errstate(invalid="ignore", over="ignore"):
        out = MAGNUS_E0_KPA * np.exp(MAGNUS_A * ta / (MAGNUS_B + ta))
    out = np.where(_valid_t(ta), out, np.nan)
    return _wrap(out, t)


# Alias gemäß docs/ARCHITECTURE.md
saturation_vapor_pressure = saturation_vapor_pressure_kpa


def dew_point(t: Any, rh: Any) -> Any:
    """Taupunkt in °C aus Temperatur (°C) und relativer Feuchte (%)."""
    ta, ra = _arr(t), _arr(rh)
    ok = _valid_t(ta) & _valid_rh(ra)
    with np.errstate(invalid="ignore", divide="ignore"):
        gamma = np.log(np.where(ok, ra, np.nan) / 100.0) + MAGNUS_A * ta / (MAGNUS_B + ta)
        td = MAGNUS_B * gamma / (MAGNUS_A - gamma)
    out = np.where(ok, td, np.nan)
    return _wrap(out, _like(t, rh))


def vapor_pressure_kpa(t: Any, rh: Any) -> Any:
    """Tatsächlicher Wasserdampfpartialdruck e = rF/100 · e_s(T) in kPa."""
    ta, ra = _arr(t), _arr(rh)
    e = np.where(_valid_rh(ra), ra / 100.0, np.nan) * _arr(saturation_vapor_pressure_kpa(ta))
    return _wrap(e, _like(t, rh))


def absolute_humidity(t: Any, rh: Any) -> Any:
    """Absolute Feuchte in g/m³: ``216,7 · e[hPa] / (273,15 + T)``."""
    ta = _arr(t)
    e_hpa = _arr(vapor_pressure_kpa(t, rh)) * 10.0
    out = WATER_VAPOR_FACTOR * e_hpa / (KELVIN + ta)
    return _wrap(out, _like(t, rh))


def vpd(t: Any, rh: Any) -> Any:
    """Dampfdruckdefizit (VPD) in kPa: ``e_s(T) · (1 − rF/100)``."""
    ta, ra = _arr(t), _arr(rh)
    out = _arr(saturation_vapor_pressure_kpa(ta)) * (1.0 - np.where(_valid_rh(ra), ra, np.nan) / 100.0)
    return _wrap(out, _like(t, rh))


def relative_humidity(t: Any, td: Any) -> Any:
    """Relative Feuchte (%) bei Temperatur ``t`` für Luft mit Taupunkt ``td``.

    ``100 · e_s(Td) / e_s(T)``. Werte > 100 % werden NICHT abgeschnitten: sie bedeuten, dass
    ``t`` unter dem Taupunkt liegt (rechnerische Übersättigung, z. B. an einer kalten Oberfläche).
    """
    es_t = _arr(saturation_vapor_pressure_kpa(t))
    es_d = _arr(saturation_vapor_pressure_kpa(td))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = 100.0 * es_d / es_t
    return _wrap(out, _like(t, td))


# ---------------------------------------------------------------------------
# DataFrame-Helfer
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").astype(float)
    return pd.Series(np.nan, index=df.index, dtype=float)


def with_calculated(df: pd.DataFrame) -> pd.DataFrame:
    """Kopie von ``df`` mit ``calc_dew_point``, ``calc_vpd``, ``calc_abs_humidity`` (aus T + rF).

    Originalspalten bleiben unverändert (Grundregel 1).
    """
    out = df.copy()
    t, rh = _col(df, TEMPERATURE), _col(df, HUMIDITY)
    out[CALC_PREFIX + DEW_POINT] = dew_point(t, rh)
    out[CALC_PREFIX + VPD] = vpd(t, rh)
    out[CALC_PREFIX + ABS_HUMIDITY] = absolute_humidity(t, rh)
    return out


def source_column(metric: str) -> str:
    return f"{metric}_source"


def effective(df: pd.DataFrame) -> pd.DataFrame:
    """Effektive Werte je Messgröße mit Herkunft.

    Liefert ``ts`` (falls vorhanden), alle Messgrößen und ``<metric>_source`` mit
    ``'measured'`` (importierter Wert), ``'calculated'`` (fehlender Wert aus T + rF berechnet)
    oder ``None`` (weder gemessen noch berechenbar). Temperatur und rF werden nie berechnet.
    Weitere Spalten (z. B. ``sensor_id``) werden übernommen.
    """
    out = pd.DataFrame(index=df.index)
    if TS in df.columns:
        out[TS] = df[TS]
    t, rh = _col(df, TEMPERATURE), _col(df, HUMIDITY)
    calc = {DEW_POINT: dew_point(t, rh), VPD: vpd(t, rh), ABS_HUMIDITY: absolute_humidity(t, rh)}
    for metric in METRICS:
        measured = _col(df, metric)
        has = measured.notna().to_numpy()
        if metric in calc:
            values = measured.where(measured.notna(), calc[metric])
            has_calc = calc[metric].notna().to_numpy() & ~has
        else:
            values = measured
            has_calc = np.zeros(len(df), dtype=bool)
        src = np.full(len(df), None, dtype=object)
        src[has] = "measured"
        src[has_calc] = "calculated"
        out[metric] = values.astype(float)
        out[source_column(metric)] = pd.Series(src, index=df.index, dtype=object)
    for extra in df.columns:
        if extra not in out.columns and extra not in METRICS:
            out[extra] = df[extra]
    return out


@dataclass
class PlausibilityItem(ResultMixin):
    metric: str
    tolerance: float
    compared: int  # Anzahl Messungen mit importiertem UND berechenbarem Wert
    exceeding: int
    exceeding_pct: float | None
    max_abs_diff: float | None
    mean_diff: float | None  # importiert − berechnet (systematische Abweichung)
    p99_abs_diff: float | None


@dataclass
class PlausibilityResult(ResultMixin):
    items: dict[str, PlausibilityItem] = field(default_factory=dict)
    ok: bool = True
    notes: list[str] = field(default_factory=list)


def plausibility(df: pd.DataFrame, tolerances: dict[str, float] | None = None) -> PlausibilityResult:
    """Vergleicht importierte Taupunkt-/VPD-/abs.-Feuchte-Werte mit der Berechnung aus T + rF.

    Abweichungen über der Toleranz deuten auf eine andere Formel, Einheitenfehler oder
    vertauschte Spalten hin. Bis ``PLAUSIBILITY_MAX_EXCEED_PCT`` % Abweichungen gelten als
    unauffällig (Rundung, Einzelwerte).
    """
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    t, rh = _col(df, TEMPERATURE), _col(df, HUMIDITY)
    calc = {DEW_POINT: dew_point(t, rh), VPD: vpd(t, rh), ABS_HUMIDITY: absolute_humidity(t, rh)}
    result = PlausibilityResult()
    for metric in DERIVED:
        imported = _col(df, metric)
        diff = (imported - calc[metric]).to_numpy()
        diff = diff[np.isfinite(diff)]
        n = len(diff)
        if n == 0:
            result.items[metric] = PlausibilityItem(metric, tol[metric], 0, 0, None, None, None, None)
            continue
        absd = np.abs(diff)
        exceed = int((absd > tol[metric] + 1e-9).sum())
        pct = exceed / n * 100.0
        result.items[metric] = PlausibilityItem(
            metric=metric,
            tolerance=tol[metric],
            compared=n,
            exceeding=exceed,
            exceeding_pct=pct,
            max_abs_diff=float(absd.max()),
            mean_diff=float(diff.mean()),
            p99_abs_diff=float(np.quantile(absd, 0.99)),
        )
        if pct > PLAUSIBILITY_MAX_EXCEED_PCT:
            result.ok = False
            result.notes.append(
                f"{metric}: {pct:.1f} % der Werte weichen um mehr als {tol[metric]:g} von der "
                "Berechnung aus Temperatur und Luftfeuchtigkeit ab."
            )
    return result
