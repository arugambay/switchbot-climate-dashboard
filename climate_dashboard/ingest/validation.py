"""Meldungen (Issues) und Plausibilitätsprüfung importierter Messwerte.

Schweregrade:

* ``ERROR`` – Import ist blockiert (z. B. keine Zeitstempel- oder Temperaturspalte).
* ``WARNING`` – Import möglich, aber der Nutzer sollte den Hinweis lesen (z. B. verworfene Werte).
* ``INFO`` – reine Information (z. B. erkannte Fahrenheit-Spalte, leere Zeilen).

Werte außerhalb physikalisch möglicher Grenzen werden auf ``NaN`` gesetzt (der Wert gilt als
ungültig), die Zeile bleibt erhalten, solange Zeitstempel und mindestens Temperatur oder
relative Luftfeuchtigkeit gültig sind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, TEMPERATURE, VPD


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Issue:
    """Eine verständliche Meldung zum Import.

    ``code`` ist ein stabiler, maschinenlesbarer Bezeichner; ``message`` ist deutscher
    Klartext für Laien; ``count`` die Anzahl betroffener Zeilen/Werte; ``examples`` einige
    Beispiele (z. B. ``"Zeile 12: 120,5"``).
    """

    severity: Severity
    code: str
    message: str
    count: int = 0
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": str(self.severity),
            "code": self.code,
            "message": self.message,
            "count": int(self.count),
            "examples": [str(e) for e in self.examples],
        }


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == Severity.ERROR for i in issues)


MAX_EXAMPLES = 5

# Plausibilitätsgrenzen -----------------------------------------------------------------
TEMP_SENSOR_MIN, TEMP_SENSOR_MAX = -40.0, 85.0  # Messbereich typischer Sensoren
TEMP_ROOM_MIN, TEMP_ROOM_MAX = -10.0, 50.0  # außerhalb: für Innenräume unplausibel (Warnung)
HUMIDITY_MIN, HUMIDITY_MAX = 0.0, 100.0
DEW_POINT_MIN, DEW_POINT_MAX = -80.0, 85.0
DEW_POINT_TOLERANCE_K = 0.5  # Taupunkt darf Temperatur um höchstens so viel übersteigen (Rundung)
ABS_HUMIDITY_MAX = 600.0  # g/m³ – weit oberhalb jeder Sättigung im Sensorbereich


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def examples_from(df: pd.DataFrame, mask: pd.Series, column: str) -> list[str]:
    """Bis zu ``MAX_EXAMPLES`` Beispiele „Zeile N: Wert“ (Index = Zeilennummer in der Datei)."""
    sel = df.loc[mask, column].head(MAX_EXAMPLES)
    return [f"Zeile {idx}: {_fmt(float(v))}" for idx, v in sel.items()]


def _flag(
    df: pd.DataFrame,
    issues: list[Issue],
    mask: pd.Series,
    column: str,
    *,
    severity: Severity,
    code: str,
    message: str,
    discard: bool,
) -> None:
    count = int(mask.sum())
    if count == 0:
        return
    issues.append(Issue(severity, code, message, count, examples_from(df, mask, column)))
    if discard:
        df.loc[mask, column] = np.nan


def check_plausibility(df: pd.DataFrame) -> tuple[pd.DataFrame, list[Issue], pd.Series]:
    """Prüft Messwerte auf Plausibilität.

    Rückgabe: (bereinigter DataFrame – Kopie –, Meldungen, Maske der Zeilen ohne gültige
    Temperatur UND ohne gültige Luftfeuchtigkeit). Der Index von ``df`` sollte die
    Zeilennummer in der Datei sein (für Beispiele).
    """
    df = df.copy()
    issues: list[Issue] = []

    if TEMPERATURE in df:
        t = df[TEMPERATURE]
        _flag(
            df,
            issues,
            (t < TEMP_SENSOR_MIN) | (t > TEMP_SENSOR_MAX),
            TEMPERATURE,
            severity=Severity.WARNING,
            code="temperature_out_of_range",
            message=(
                f"Temperaturwerte außerhalb des Sensor-Messbereichs ({_fmt(TEMP_SENSOR_MIN)} bis "
                f"{_fmt(TEMP_SENSOR_MAX)} °C) wurden als ungültig verworfen."
            ),
            discard=True,
        )
        t = df[TEMPERATURE]
        _flag(
            df,
            issues,
            (t < TEMP_ROOM_MIN) | (t > TEMP_ROOM_MAX),
            TEMPERATURE,
            severity=Severity.WARNING,
            code="temperature_implausible_room",
            message=(
                f"Einige Temperaturen liegen unter {_fmt(TEMP_ROOM_MIN)} °C oder über {_fmt(TEMP_ROOM_MAX)} °C. "
                "Für einen Innenraum ist das ungewöhnlich – stand der Sensor draußen oder an einer "
                "Wärmequelle? Die Werte wurden übernommen."
            ),
            discard=False,
        )

    if HUMIDITY in df:
        h = df[HUMIDITY]
        _flag(
            df,
            issues,
            (h < HUMIDITY_MIN) | (h > HUMIDITY_MAX),
            HUMIDITY,
            severity=Severity.WARNING,
            code="humidity_out_of_range",
            message="Luftfeuchtigkeitswerte unter 0 % oder über 100 % sind unmöglich und wurden verworfen.",
            discard=True,
        )
        h = df[HUMIDITY]
        _flag(
            df,
            issues,
            (h == HUMIDITY_MIN) | (h == HUMIDITY_MAX),
            HUMIDITY,
            severity=Severity.WARNING,
            code="humidity_extreme",
            message=(
                "Einige Messungen zeigen genau 0 % oder 100 % Luftfeuchtigkeit. Das kann auf einen "
                "Sensorfehler oder Nässe am Sensor hindeuten. Die Werte wurden übernommen."
            ),
            discard=False,
        )

    if DEW_POINT in df:
        d = df[DEW_POINT]
        _flag(
            df,
            issues,
            (d < DEW_POINT_MIN) | (d > DEW_POINT_MAX),
            DEW_POINT,
            severity=Severity.WARNING,
            code="dew_point_out_of_range",
            message="Taupunktwerte außerhalb des möglichen Bereichs wurden verworfen.",
            discard=True,
        )
        if TEMPERATURE in df:
            d = df[DEW_POINT]
            _flag(
                df,
                issues,
                d > df[TEMPERATURE] + DEW_POINT_TOLERANCE_K,
                DEW_POINT,
                severity=Severity.WARNING,
                code="dew_point_above_temperature",
                message=(
                    "Der Taupunkt kann nicht über der Lufttemperatur liegen. Betroffene Taupunktwerte "
                    "wurden verworfen (Temperatur und Luftfeuchtigkeit bleiben erhalten)."
                ),
                discard=True,
            )

    if VPD in df:
        _flag(
            df,
            issues,
            df[VPD] < 0,
            VPD,
            severity=Severity.WARNING,
            code="vpd_negative",
            message="Negative Werte für das Dampfdruckdefizit (VPD) sind unmöglich und wurden verworfen.",
            discard=True,
        )

    if ABS_HUMIDITY in df:
        a = df[ABS_HUMIDITY]
        _flag(
            df,
            issues,
            (a < 0) | (a > ABS_HUMIDITY_MAX),
            ABS_HUMIDITY,
            severity=Severity.WARNING,
            code="abs_humidity_out_of_range",
            message="Werte der absoluten Luftfeuchtigkeit außerhalb des möglichen Bereichs wurden verworfen.",
            discard=True,
        )

    t_ok = df[TEMPERATURE].notna() if TEMPERATURE in df else pd.Series(False, index=df.index)
    h_ok = df[HUMIDITY].notna() if HUMIDITY in df else pd.Series(False, index=df.index)
    no_core = ~(t_ok | h_ok)
    return df, issues, no_core
