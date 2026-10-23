"""Gemeinsame Domänenobjekte.

Alle Module (Import, Analyse, Speicherung, Berichte, Oberfläche) tauschen Daten über diese
Klassen bzw. über den kanonischen Messdaten-DataFrame aus (siehe ``MEASUREMENT_COLUMNS``).

IDs sind stabile UUID-Strings. Sichtbare Namen (Raumname, Sensorname) sind frei änderbar
und werden niemals als Schlüssel verwendet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Kanonischer Messdaten-DataFrame
# ---------------------------------------------------------------------------
# Spalte ``ts``: tz-aware Zeitstempel in UTC (pandas datetime64[ns, UTC]).
# Messgrößen: float64, NaN = nicht vorhanden. Es sind immer die ORIGINAL importierten Werte;
# berechnete Werte werden nie in diese Spalten geschrieben, sondern bei Bedarf in Spalten mit
# Präfix ``calc_`` (z. B. ``calc_dew_point``) ergänzt.
TS = "ts"
TEMPERATURE = "temperature"  # °C
HUMIDITY = "humidity"  # % relative Luftfeuchtigkeit
DEW_POINT = "dew_point"  # °C
VPD = "vpd"  # kPa
ABS_HUMIDITY = "abs_humidity"  # g/m³
RAW_TIMESTAMP = "raw_timestamp"  # Originaltext des Zeitstempels aus der CSV (optional)
SENSOR_ID = "sensor_id"  # nur in Mehr-Sensor-Abfragen vorhanden

METRICS: tuple[str, ...] = (TEMPERATURE, HUMIDITY, DEW_POINT, VPD, ABS_HUMIDITY)
MEASUREMENT_COLUMNS: tuple[str, ...] = (TS, *METRICS)

METRIC_LABELS: dict[str, str] = {
    TEMPERATURE: "Temperatur",
    HUMIDITY: "Relative Luftfeuchtigkeit",
    DEW_POINT: "Taupunkt",
    VPD: "Dampfdruckdefizit (VPD)",
    ABS_HUMIDITY: "Absolute Luftfeuchtigkeit",
}
METRIC_UNITS: dict[str, str] = {
    TEMPERATURE: "°C",
    HUMIDITY: "%",
    DEW_POINT: "°C",
    VPD: "kPa",
    ABS_HUMIDITY: "g/m³",
}
# Anzeige-Nachkommastellen (vermeidet Scheingenauigkeit)
METRIC_DECIMALS: dict[str, int] = {
    TEMPERATURE: 1,
    HUMIDITY: 0,
    DEW_POINT: 1,
    VPD: 2,
    ABS_HUMIDITY: 1,
}


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Räume und Sensoren
# ---------------------------------------------------------------------------
@dataclass
class Room:
    id: str
    name: str
    description: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


@dataclass
class Sensor:
    id: str
    room_id: str
    name: str
    device_info: str | None = None  # z. B. Modell oder Seriennummer, frei
    timezone: str = "Europe/Berlin"  # Zeitzone der naiven Zeitstempel in den CSV-Dateien
    expected_interval_s: int | None = None  # None = automatisch erkennen
    created_at: datetime = field(default_factory=utcnow)
    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


# ---------------------------------------------------------------------------
# Importvorgänge und Konflikte
# ---------------------------------------------------------------------------
class ImportStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class ImportRecord:
    id: str
    sensor_id: str
    room_id: str
    file_name: str
    file_sha256: str
    status: ImportStatus = ImportStatus.PENDING
    rows_read: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0  # bereits vorhanden (identisch) – inkl. Duplikate innerhalb der Datei
    rows_invalid: int = 0  # ungültig/ignoriert
    rows_conflict: int = 0
    ts_min: datetime | None = None
    ts_max: datetime | None = None
    expected_interval_s: int | None = None
    observed_interval_s: int | None = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)  # Erkennung (Trennzeichen, Spalten …)
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


class ConflictResolution(StrEnum):
    KEEP_EXISTING = "keep_existing"
    USE_INCOMING = "use_incoming"


@dataclass
class MeasurementConflict:
    """Gleicher Sensor + gleicher Zeitpunkt, aber abweichende Messwerte.

    Es wird nichts automatisch überschrieben. ``existing``/``incoming`` enthalten die Werte
    als ``{metric: float|None}``.
    """

    id: str
    sensor_id: str
    import_id: str
    ts: datetime
    existing: dict[str, float | None]
    incoming: dict[str, float | None]
    resolution: ConflictResolution | None = None
    resolved_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Berichte, Versionen und Freigaben
# ---------------------------------------------------------------------------
@dataclass
class Report:
    id: str
    room_id: str
    title: str
    current_version_id: str | None = None  # die Version, die Freigabelinks anzeigen
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    archived_at: datetime | None = None


@dataclass
class ReportVersion:
    """Unveränderlicher Schnappschuss eines Berichts.

    ``content`` ist das vollständige, bereits berechnete Berichts-JSON (siehe
    docs/ARCHITECTURE.md, Abschnitt „Berichts-Snapshot“). Die öffentliche Ansicht und der
    PDF-Export rendern ausschließlich aus ``content`` – nie aus Rohdaten.
    """

    id: str
    report_id: str
    version: int
    period_start: datetime
    period_end: datetime
    config: dict[str, Any]
    content: dict[str, Any]
    data_fingerprint: str  # Hash über die zugrunde liegenden Messungen
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Share:
    """Freigabelink. Der Klartext-Token wird nur einmal bei Erstellung angezeigt;
    gespeichert wird ausschließlich ``token_hash`` (SHA-256, hex)."""

    id: str
    report_id: str
    token_hash: str
    token_hint: str  # letzte 4 Zeichen zur Wiedererkennung
    label: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------
@dataclass
class AnalysisSettings:
    """Konfigurierbare Schwellenwerte. Defaults sind bewusst konservative Orientierungswerte,
    keine Grenzwerte im rechtlichen Sinn."""

    humidity_elevated: float = 60.0  # % rF – erhöht
    humidity_high: float = 70.0  # % rF – hoch
    humidity_low: float = 35.0  # % rF – trocken
    min_episode_minutes: int = 120  # ab wann eine erhöhte Phase als „anhaltend“ gilt
    episode_merge_gap_minutes: int = 30  # kurze Unterbrechungen zusammenfassen
    dew_point_spread_warn: float = 3.0  # K Abstand Raumluft–Taupunkt, ab dem hingewiesen wird
    surface_offset_k: float | None = None  # angenommene Differenz Raumluft→kalte Oberfläche (Annahme)
    night_start_hour: int = 22
    night_end_hour: int = 6
    gap_factor: float = 3.0  # Lücke = Abstand > gap_factor × erwartetes Intervall
    # … mindestens jedoch so viele Minuten. 180 statt 60, weil SwitchBot-Exporte häufig einzelne
    # Messungen auslassen (60–120-min-Abstände bei 30-min-Raster); solche kurzen Aussetzer sollen
    # anhaltende Feuchtephasen nicht zerschneiden. Längere Abstände bleiben Datenlücken.
    min_gap_minutes: int = 180
    timezone: str = "Europe/Berlin"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AnalysisSettings:
        data = data or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
