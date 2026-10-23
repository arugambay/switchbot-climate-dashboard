"""Deutsche Formatierung für die Oberfläche – ohne Abhängigkeit von System-Locales.

Konventionen (für alle Seiten, Berichte und Diagramm-Hover verbindlich):

* Zahlen: Tausenderpunkt, Dezimalkomma → ``1.234,5``.
* Negative Werte verwenden das typografische Minus (U+2212 ``−``), Deltas immer mit
  Vorzeichen (``+1,2 K``, ``−0,4 K``, ``±0,0 K``).
* Zwischen Zahl und Einheit steht ein schmales geschütztes Leerzeichen (U+202F), auch vor
  ``%`` (DIN 5008) → ``21,4 °C``, ``57 %``.
* Fehlende Werte (``None``, ``NaN``, ``NaT``) werden als Gedankenstrich ``–`` angezeigt.
* Datum/Zeit: ``22.10.2026, 21:45`` in der lokalen Zeitzone (Standard ``Europe/Berlin``).
  Naive Zeitstempel gelten als UTC (intern wird immer UTC verwendet).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from climate_dashboard.domain.models import (
    DEW_POINT,
    HUMIDITY,
    METRIC_DECIMALS,
    METRIC_LABELS,
    METRIC_UNITS,
    TEMPERATURE,
)

MISSING = "–"  # Halbgeviertstrich für fehlende Werte
MINUS = "−"  # typografisches Minus
NARROW_NBSP = " "  # schmales geschütztes Leerzeichen zwischen Zahl und Einheit
NBSP = " "
DEFAULT_TZ = "Europe/Berlin"

MONTH_NAMES: tuple[str, ...] = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
# Kurzform für Diagramm-Achsen (immer 3 Zeichen, ohne Punkt – ruhiger in Ticks)
MONTH_ABBR: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
)
WEEKDAY_NAMES: tuple[str, ...] = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)
WEEKDAY_ABBR: tuple[str, ...] = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Meteorologische Jahreszeiten (Dez–Feb Winter usw.)
SEASON_LABELS: dict[str, str] = {
    "winter": "Winter",
    "spring": "Frühling",
    "summer": "Sommer",
    "autumn": "Herbst",
}
_SEASON_ALIASES: dict[str, str] = {
    "winter": "winter",
    "djf": "winter",
    "spring": "spring",
    "frühling": "spring",
    "fruehling": "spring",
    "mam": "spring",
    "summer": "summer",
    "sommer": "summer",
    "jja": "summer",
    "autumn": "autumn",
    "fall": "autumn",
    "herbst": "autumn",
    "son": "autumn",
}
SEASON_ORDER: tuple[str, ...] = ("winter", "spring", "summer", "autumn")

# Einheit für Differenzen je Messgröße (Temperaturdifferenzen in Kelvin)
DELTA_UNITS: dict[str, str] = {
    TEMPERATURE: "K",
    DEW_POINT: "K",
    "dew_point_spread": "K",
    "spread": "K",
}


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def is_missing(value: Any) -> bool:
    """True für None, NaN, NaT und pandas.NA."""
    if value is None:
        return True
    try:
        import pandas as pd

        if value is pd.NaT or value is pd.NA:
            return True
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, float):
        return math.isnan(value)
    try:
        return bool(value != value)  # NaN-Eigenschaft (numpy-Skalare)
    except (TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def metric_unit(metric: str) -> str:
    return METRIC_UNITS.get(metric, "")


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def metric_decimals(metric: str, default: int = 1) -> int:
    return METRIC_DECIMALS.get(metric, default)


def with_unit(text: str, unit: str | None) -> str:
    """Hängt eine Einheit mit schmalem geschütztem Leerzeichen an."""
    if not unit or text == MISSING:
        return text
    return f"{text}{NARROW_NBSP}{unit}"


# ---------------------------------------------------------------------------
# Zahlen
# ---------------------------------------------------------------------------
def fmt_number(value: Any, decimals: int = 0, *, sign: bool = False, grouping: bool = True) -> str:
    """``1234.5`` → ``"1.234,5"`` (deutsch, locale-unabhängig).

    ``sign=True`` erzwingt ein Vorzeichen (``+``/``−``, ``±`` bei 0).
    """
    f = _as_float(value)
    if f is None:
        return MISSING
    decimals = max(0, int(decimals))
    rounded = round(f, decimals)
    if rounded == 0:
        rounded = 0.0  # verhindert "−0,0"
    text = f"{abs(rounded):,.{decimals}f}" if grouping else f"{abs(rounded):.{decimals}f}"
    text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    if rounded < 0:
        return f"{MINUS}{text}"
    if sign:
        return f"±{text}" if rounded == 0 else f"+{text}"
    return text


def fmt_int(value: Any) -> str:
    return fmt_number(value, 0)


def fmt_metric(value: Any, metric: str, decimals: int | None = None) -> str:
    """Wert mit Einheit gemäß ``METRIC_UNITS``/``METRIC_DECIMALS`` → ``"21,4 °C"``, ``"57 %"``."""
    d = metric_decimals(metric) if decimals is None else decimals
    return with_unit(fmt_number(value, d), metric_unit(metric))


def fmt_delta(value: Any, metric: str, decimals: int | None = None, *, short: bool = False) -> str:
    """Differenz mit Vorzeichen.

    * Temperatur/Taupunkt: Kelvin → ``"+1,2 K"``
    * relative Feuchte: Prozentpunkte → ``"+3 Prozentpunkte"`` (``short=True``: ``"+3 %-Pkt."``)
    * sonst die Einheit der Messgröße.
    """
    d = metric_decimals(metric) if decimals is None else decimals
    f = _as_float(value)
    if f is None:
        return MISSING
    text = fmt_number(f, d, sign=True)
    if metric == HUMIDITY:
        if short:
            return with_unit(text, "%-Pkt.")
        singular = round(abs(f), d) == 1
        return f"{text}{NBSP}{'Prozentpunkt' if singular else 'Prozentpunkte'}"
    unit = DELTA_UNITS.get(metric, metric_unit(metric))
    return with_unit(text, unit)


def fmt_percent(value: Any, decimals: int = 0, *, ratio: bool = False, sign: bool = False) -> str:
    """Prozentwert → ``"57 %"``. ``ratio=True``: Eingabe ist ein Anteil 0…1 (``0.57`` → ``"57 %"``)."""
    f = _as_float(value)
    if f is None:
        return MISSING
    if ratio:
        f *= 100
    return with_unit(fmt_number(f, decimals, sign=sign), "%")


# ---------------------------------------------------------------------------
# Dauern
# ---------------------------------------------------------------------------
def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n}{NBSP}{singular if n == 1 else plural}"


def fmt_duration(seconds: Any, *, max_units: int = 2) -> str:
    """Dauer in Sekunden (oder ``timedelta``) → ``"3 Std. 20 Min."`` / ``"2 Tage 4 Std."``.

    Gerundet auf Minuten; Einheiten mit Wert 0 werden ausgelassen; höchstens ``max_units``
    Einheiten (die kleinste angezeigte wird gerundet). Unter einer Minute → ``"< 1 Min."``.
    """
    if isinstance(seconds, timedelta):
        seconds = seconds.total_seconds()
    else:
        try:
            import pandas as pd

            if isinstance(seconds, pd.Timedelta):
                seconds = None if seconds is pd.NaT else seconds.total_seconds()
        except ImportError:  # pragma: no cover
            pass
    f = _as_float(seconds)
    if f is None:
        return MISSING
    negative = f < 0
    f = abs(f)
    if f == 0:
        return f"0{NBSP}Min."
    if f < 60:
        return f"<{NBSP}1{NBSP}Min."

    units = [(86400, "Tag", "Tage"), (3600, "Std.", "Std."), (60, "Min.", "Min.")]
    total_min = int(round(f / 60))
    # höchste Einheit bestimmen
    start = 0
    while start < len(units) - 1 and total_min * 60 < units[start][0]:
        start += 1
    used = units[start : start + max(1, max_units)]
    smallest = used[-1][0]
    remaining = int(round(total_min * 60 / smallest)) * smallest
    parts: list[str] = []
    for size, sing, plur in used:
        n = remaining // size
        remaining -= n * size
        if n:
            parts.append(_plural(int(n), sing, plur))
    text = " ".join(parts) if parts else f"0{NBSP}Min."
    return f"{MINUS}{text}" if negative else text


# ---------------------------------------------------------------------------
# Datum und Zeit
# ---------------------------------------------------------------------------
def to_local(value: Any, tz: str | None = DEFAULT_TZ) -> datetime | None:
    """Konvertiert datetime/pandas.Timestamp/ISO-String in die lokale Zeitzone.

    Naive Werte gelten als UTC. ``date``-Objekte werden unverändert zu Mitternacht.
    """
    if is_missing(value):
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(ZoneInfo(tz or DEFAULT_TZ))
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _date_part(d: datetime | date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


def fmt_datetime(value: Any, tz: str | None = DEFAULT_TZ, *, seconds: bool = False) -> str:
    """→ ``"22.10.2026, 21:45"`` in lokaler Zeit."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return _date_part(value)
    d = to_local(value, tz)
    if d is None:
        return MISSING
    time = f"{d.hour:02d}:{d.minute:02d}" + (f":{d.second:02d}" if seconds else "")
    return f"{_date_part(d)}, {time}"


def fmt_time(value: Any, tz: str | None = DEFAULT_TZ) -> str:
    d = to_local(value, tz)
    return MISSING if d is None else f"{d.hour:02d}:{d.minute:02d}"


def fmt_date(value: Any, tz: str | None = DEFAULT_TZ, *, style: str = "short") -> str:
    """``style="short"`` → ``"22.10.2026"``; ``"long"`` → ``"22. Oktober 2026"``;
    ``"weekday"`` → ``"Do, 22.10.2026"``."""
    if isinstance(value, date) and not isinstance(value, datetime):
        d: datetime | date | None = value
    else:
        d = to_local(value, tz)
    if d is None:
        return MISSING
    if style == "long":
        return f"{d.day}. {MONTH_NAMES[d.month - 1]} {d.year}"
    if style == "weekday":
        return f"{WEEKDAY_ABBR[d.weekday()]}, {_date_part(d)}"
    return _date_part(d)


def fmt_date_range(start: Any, end: Any, tz: str | None = DEFAULT_TZ, *, end_exclusive: bool = False) -> str:
    """Kompakter Zeitraum: ``"01.03.–22.10.2026"`` (gleiches Jahr), sonst
    ``"15.12.2025–10.01.2026"``; gleicher Tag → ``"22.10.2026"``.

    ``end_exclusive=True``: ein Ende genau um Mitternacht zählt nicht mehr dazu
    (``[01.03. 00:00, 01.04. 00:00)`` → ``"01.03.–31.03.2026"``).
    """

    def as_local(v: Any) -> datetime | date | None:
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        return to_local(v, tz)

    s, e = as_local(start), as_local(end)
    if s is None and e is None:
        return MISSING
    if s is None:
        return f"bis {_date_part(e)}"  # type: ignore[arg-type]
    if e is None:
        return f"ab {_date_part(s)}"
    if end_exclusive:
        is_midnight = not isinstance(e, datetime) or (e.hour == 0 and e.minute == 0 and e.second == 0)
        if is_midnight:
            e = e - timedelta(days=1)
    s_d = s.date() if isinstance(s, datetime) else s
    e_d = e.date() if isinstance(e, datetime) else e
    if e_d < s_d:
        e_d = s_d
    if s_d == e_d:
        return _date_part(s_d)
    if s_d.year == e_d.year:
        return f"{s_d.day:02d}.{s_d.month:02d}.–{_date_part(e_d)}"
    return f"{_date_part(s_d)}–{_date_part(e_d)}"


def month_name(month: int, *, short: bool = False) -> str:
    if not 1 <= int(month) <= 12:
        return MISSING
    return (MONTH_ABBR if short else MONTH_NAMES)[int(month) - 1]


def fmt_month(value: Any, *, short: bool = False, tz: str | None = None) -> str:
    """Monat → ``"Oktober 2026"`` (``short``: ``"Okt 2026"``).

    Akzeptiert ``"2026-10"``, ``date``/``datetime``/``Timestamp``/``Period``. Ohne ``tz`` werden
    Zeitstempel nicht umgerechnet (Monatswerte sind bereits lokal gruppiert).
    """
    if is_missing(value):
        return MISSING
    year = month = None
    if isinstance(value, str):
        try:
            y, m = value[:7].split("-")
            year, month = int(y), int(m)
        except ValueError:
            return value
    elif hasattr(value, "year") and hasattr(value, "month"):
        v = value
        if tz is not None and isinstance(v, datetime):
            v = to_local(v, tz) or v
        year, month = int(v.year), int(v.month)
    if year is None or month is None:
        return str(value)
    return f"{month_name(month, short=short)} {year}"


def weekday_name(index: int, *, short: bool = False) -> str:
    """0 = Montag … 6 = Sonntag."""
    return (WEEKDAY_ABBR if short else WEEKDAY_NAMES)[int(index) % 7]


def season_key(month: int) -> str:
    """Meteorologische Jahreszeit für einen Monat (1–12)."""
    return ("winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "autumn",
            "autumn", "autumn", "winter")[int(month) - 1]  # fmt: skip


def season_label(key_or_month: Any, year: int | None = None) -> str:
    """``"summer"``/``"Sommer"``/``"JJA"``/Monat 7 → ``"Sommer"``; mit ``year`` → ``"Sommer 2026"``.

    Winter überspannt den Jahreswechsel: ``season_label("winter", 2026)`` → ``"Winter 2025/26"``
    (Konvention: ``year`` ist das Jahr, in dem der Winter endet – Januar/Februar).
    """
    if is_missing(key_or_month):
        return MISSING
    if isinstance(key_or_month, int | float):
        key = season_key(int(key_or_month))
    else:
        key = _SEASON_ALIASES.get(str(key_or_month).strip().lower(), "")
        if not key:
            return str(key_or_month)
    label = SEASON_LABELS[key]
    if year is None:
        return label
    if key == "winter":
        return f"{label} {int(year) - 1}/{int(year) % 100:02d}"
    return f"{label} {int(year)}"


def fmt_bool(value: Any) -> str:
    if is_missing(value):
        return MISSING
    return "Ja" if value else "Nein"


def fmt_value(value: Any, metric: str | None = None) -> str:
    """Generischer Formatierer für Tabellen/Schlüssel-Wert-Listen."""
    if is_missing(value):
        return MISSING
    if isinstance(value, bool):
        return fmt_bool(value)
    if metric and metric in METRIC_UNITS:
        return fmt_metric(value, metric)
    if isinstance(value, datetime):
        return fmt_datetime(value)
    if isinstance(value, date):
        return fmt_date(value)
    if isinstance(value, int):
        return fmt_int(value)
    if isinstance(value, float):
        return fmt_number(value, 1)
    if isinstance(value, Mapping):
        return ", ".join(f"{k}: {fmt_value(v)}" for k, v in value.items())
    return str(value)
