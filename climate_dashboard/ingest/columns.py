"""Spaltenerkennung für CSV-Exporte (SwitchBot und ähnliche Formate).

Spaltenköpfe werden normalisiert (Groß/Klein, Leerzeichen, Unterstriche, Bindestriche,
°/℃-Varianten, Umlaute) und über Aliase einer kanonischen Größe zugeordnet. Die Einheit in
Klammern (z. B. ``(°F)``) wird separat ausgewertet, damit Fahrenheit-Spalten erkannt und in
°C umgerechnet werden können.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, METRICS, TEMPERATURE, VPD

# Rollen für Zeitspalten (keine Messgrößen)
TIMESTAMP_ROLE = "timestamp"
DATE_ROLE = "date"
TIME_ROLE = "time"

# Normalisierte Namen (ohne Einheit) -> kanonische Rolle/Messgröße
_ALIASES: dict[str, tuple[str, ...]] = {
    TIMESTAMP_ROLE: (
        "timestamp",
        "datetime",
        "dateandtime",
        "datumzeit",
        "datumuhrzeit",
        "zeitstempel",
        "zeitpunkt",
        "messzeit",
        "localtime",
        "unixtime",
        "epoch",
    ),
    DATE_ROLE: ("date", "datum", "day", "tag"),
    TIME_ROLE: ("time", "zeit", "uhrzeit", "clock"),
    TEMPERATURE: (
        "temperature",
        "temperaturecelsius",
        "temperaturec",
        "temperaturefahrenheit",
        "temperaturef",
        "temp",
        "tempc",
        "tempf",
        "temperatur",
        "lufttemperatur",
        "raumtemperatur",
    ),
    HUMIDITY: (
        "humidity",
        "relativehumidity",
        "relhumidity",
        "rh",
        "hum",
        "luftfeuchtigkeit",
        "luftfeuchte",
        "relativeluftfeuchtigkeit",
        "relativeluftfeuchte",
        "relluftfeuchte",
        "feuchte",
        "feuchtigkeit",
        "rf",
    ),
    DEW_POINT: (
        "dpt",
        "dewpoint",
        "dewpointcelsius",
        "dewpointfahrenheit",
        "dp",
        "taupunkt",
        "taupunkttemperatur",
    ),
    VPD: ("vpd", "vaporpressuredeficit", "vapourpressuredeficit", "dampfdruckdefizit", "saettigungsdefizit"),
    ABS_HUMIDITY: (
        "absolutehumidity",
        "abshumidity",
        "ah",
        "absolutefeuchte",
        "absoluteluftfeuchtigkeit",
        "absoluteluftfeuchte",
        "absfeuchte",
    ),
}

_LOOKUP: dict[str, str] = {alias: role for role, aliases in _ALIASES.items() for alias in aliases}

# Messgrößen, die als Temperatur (°C/°F) vorliegen können
TEMPERATURE_LIKE: tuple[str, ...] = (TEMPERATURE, DEW_POINT)

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _normalize_text(text: str) -> str:
    """NFKC (℃ → °C), casefold, Umlaute ersetzen."""
    return unicodedata.normalize("NFKC", text).casefold().translate(_UMLAUTS)


def split_header(header: str) -> tuple[str, str]:
    """Teilt einen Spaltenkopf in normalisierten Namen und normalisierte Einheit.

    ``"Temperature_Fahrenheit(°F)"`` → ``("temperaturefahrenheit", "°f")``;
    ``"temperature(℃)"`` → ``("temperature", "°c")``; ``"Temp °C"`` → ``("temp", "°c")``.
    """
    text = _normalize_text(header.strip().strip('"').strip())
    unit = ""
    match = re.search(r"[\(\[]([^\)\]]*)[\)\]]\s*$", text)
    if match:
        unit = match.group(1)
        text = text[: match.start()]
    else:
        tail = re.search(r"\s*(°\s*[cf]|%|kpa|g/m3|g/m³)\s*$", text)
        if tail:
            unit = tail.group(1)
            text = text[: tail.start()]
    name = re.sub(r"[\s_\-\.\:/]+", "", text)
    unit = re.sub(r"\s+", "", unit).replace("deg", "°")
    return name, unit


def is_fahrenheit(name: str, unit: str) -> bool:
    """True, wenn Name oder Einheit auf Fahrenheit hinweisen."""
    return unit in {"°f", "f", "fahrenheit"} or "fahrenheit" in name or name in {"tempf", "temperaturef"}


@dataclass
class ColumnMapping:
    """Ergebnis der Spaltenzuordnung.

    ``roles``: kanonische Rolle → Original-Spaltenkopf (Zeitstempel, Datum, Uhrzeit, Messgrößen).
    ``fahrenheit``: Messgrößen, deren Spalte in °F vorliegt.
    ``unknown``: nicht zuordenbare Spalten. ``ignored``: doppelt zugeordnete Spalten (nicht genutzt).
    """

    roles: dict[str, str] = field(default_factory=dict)
    fahrenheit: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def metrics(self) -> list[str]:
        return [m for m in METRICS if m in self.roles]

    @property
    def missing_metrics(self) -> list[str]:
        return [m for m in METRICS if m not in self.roles]

    @property
    def has_time(self) -> bool:
        return TIMESTAMP_ROLE in self.roles or DATE_ROLE in self.roles or TIME_ROLE in self.roles


def classify_header(header: str) -> tuple[str | None, bool]:
    """Ordnet einen einzelnen Spaltenkopf zu. Rückgabe: (Rolle oder None, ist_fahrenheit)."""
    name, unit = split_header(header)
    role = _LOOKUP.get(name)
    if role is None:
        # Varianten wie "temperature_celsius_c" o. Ä.: Präfix-Treffer bei eindeutigen Stämmen
        for stem, stem_role in (
            ("temperatur", TEMPERATURE),
            ("relativehumidity", HUMIDITY),
            ("luftfeuchtigkeit", HUMIDITY),
            ("dewpoint", DEW_POINT),
            ("taupunkt", DEW_POINT),
            ("absolutehumidity", ABS_HUMIDITY),
            ("timestamp", TIMESTAMP_ROLE),
        ):
            if name.startswith(stem):
                role = stem_role
                break
    if role is None:
        return None, False
    fahrenheit = role in TEMPERATURE_LIKE and is_fahrenheit(name, unit)
    return role, fahrenheit


def map_columns(headers: list[str]) -> ColumnMapping:
    """Ordnet alle Spaltenköpfe zu (beliebige Reihenfolge).

    Gibt es mehrere Spalten für dieselbe Größe (z. B. °C und °F), wird die °C-Spalte bzw.
    die erste Spalte genutzt; weitere landen in ``ignored``.
    """
    mapping = ColumnMapping()
    for header in headers:
        role, fahrenheit = classify_header(str(header))
        if role is None:
            mapping.unknown.append(str(header))
            continue
        if role in mapping.roles:
            # Celsius bevorzugen, falls bisher Fahrenheit gewählt war
            if role in mapping.fahrenheit and not fahrenheit:
                mapping.ignored.append(mapping.roles[role])
                mapping.roles[role] = str(header)
                mapping.fahrenheit.discard(role)
            else:
                mapping.ignored.append(str(header))
            continue
        mapping.roles[role] = str(header)
        if fahrenheit:
            mapping.fahrenheit.add(role)
    return mapping
