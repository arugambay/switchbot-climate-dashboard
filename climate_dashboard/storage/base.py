"""Schnittstelle für die Datenspeicherung.

Es gibt zwei Implementierungen:

* ``SqliteRepository`` – lokaler Entwicklungsmodus und Tests (eine Datei, ein Nutzer).
* ``SupabaseRepository`` – Produktion (PostgreSQL, Row Level Security pro Nutzer).

Die Oberfläche und alle Fachmodule sprechen ausschließlich mit dieser Schnittstelle.
Zugriffskontrolle passiert in der Datenbank (RLS) bzw. in der Repository-Implementierung,
niemals nur in der Oberfläche.

Zeitangaben: alle ``datetime``-Parameter und -Rückgaben sind tz-aware (UTC).
Messdaten-DataFrames folgen ``domain.models.MEASUREMENT_COLUMNS`` (Spalte ``ts`` in UTC).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from climate_dashboard.domain.models import (
    ConflictResolution,
    ImportRecord,
    MeasurementConflict,
    Report,
    ReportVersion,
    Room,
    Sensor,
    Share,
)


class RepositoryError(RuntimeError):
    """Allgemeiner Speicherfehler (Verbindung, Constraint, Berechtigung)."""


class NotFoundError(RepositoryError):
    """Objekt existiert nicht oder gehört nicht dem angemeldeten Nutzer."""


class IntegrityError(RepositoryError):
    """Verletzung einer Eindeutigkeits- oder Fremdschlüsselbedingung."""


@runtime_checkable
class Repository(Protocol):
    # --- Räume -------------------------------------------------------------
    def list_rooms(self, include_archived: bool = False) -> list[Room]: ...
    def get_room(self, room_id: str) -> Room: ...
    def create_room(self, name: str, description: str | None = None) -> Room: ...
    def rename_room(self, room_id: str, new_name: str) -> Room: ...
    def update_room(self, room_id: str, *, description: str | None = None) -> Room: ...
    def archive_room(self, room_id: str) -> Room: ...
    def unarchive_room(self, room_id: str) -> Room: ...
    def delete_room(self, room_id: str) -> None:
        """Löscht Raum inkl. Sensoren, Messungen, Importprotokollen, Berichten und Freigaben.
        Die Oberfläche muss vorher die Auswirkungen anzeigen und bestätigen lassen."""
        ...

    # --- Sensoren ----------------------------------------------------------
    def list_sensors(self, room_id: str | None = None, include_archived: bool = False) -> list[Sensor]: ...
    def get_sensor(self, sensor_id: str) -> Sensor: ...
    def create_sensor(
        self,
        room_id: str,
        name: str,
        *,
        device_info: str | None = None,
        timezone: str = "Europe/Berlin",
        expected_interval_s: int | None = None,
    ) -> Sensor: ...
    def update_sensor(self, sensor_id: str, **fields: Any) -> Sensor:
        """Erlaubte Felder: name, device_info, timezone, expected_interval_s, room_id."""
        ...
    def archive_sensor(self, sensor_id: str) -> Sensor: ...
    def unarchive_sensor(self, sensor_id: str) -> Sensor: ...

    # --- Messungen ---------------------------------------------------------
    def load_measurements(
        self, sensor_ids: list[str], start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        """Messungen im halboffenen Intervall [start, end), sortiert nach ``ts``.
        Spalten: ``sensor_id`` + MEASUREMENT_COLUMNS. Leerer DataFrame mit diesen Spalten,
        wenn nichts vorhanden ist."""
        ...

    def measurement_extent(self, sensor_id: str) -> tuple[datetime | None, datetime | None, int]:
        """(erste Messung, letzte Messung, Anzahl) – ohne alle Daten zu laden."""
        ...

    def insert_measurements(self, sensor_id: str, import_id: str, df: pd.DataFrame) -> int:
        """Fügt neue Messungen ein. Bereits vorhandene (sensor_id, ts) werden NICHT
        überschrieben (ON CONFLICT DO NOTHING). Gibt die Anzahl tatsächlich eingefügter
        Zeilen zurück. ``df`` enthält MEASUREMENT_COLUMNS und optional ``raw_timestamp``."""
        ...

    def delete_measurements_of_import(self, import_id: str) -> int:
        """Rollback: entfernt alle Messungen, die durch diesen Import eingefügt wurden."""
        ...

    def replace_measurement_values(self, sensor_id: str, ts: datetime, values: dict[str, float | None]) -> None:
        """Nur für ausdrücklich bestätigte Konfliktauflösung (``USE_INCOMING``)."""
        ...

    # --- Importe -----------------------------------------------------------
    def create_import(self, record: ImportRecord) -> ImportRecord: ...
    def update_import(self, record: ImportRecord) -> ImportRecord: ...
    def get_import(self, import_id: str) -> ImportRecord: ...
    def list_imports(self, room_id: str | None = None, sensor_id: str | None = None) -> list[ImportRecord]: ...
    def find_imports_by_hash(self, sensor_id: str, file_sha256: str) -> list[ImportRecord]: ...

    # --- Konflikte ---------------------------------------------------------
    def add_conflicts(self, conflicts: list[MeasurementConflict]) -> None: ...
    def delete_conflicts_of_import(self, import_id: str) -> int:
        """Rollback: entfernt alle Konfliktdatensätze, die dieser Import angelegt hat.
        Gibt die Anzahl entfernter Datensätze zurück."""
        ...
    def list_conflicts(
        self, sensor_id: str | None = None, unresolved_only: bool = True
    ) -> list[MeasurementConflict]: ...
    def resolve_conflict(self, conflict_id: str, resolution: ConflictResolution) -> MeasurementConflict:
        """Bei USE_INCOMING werden die Messwerte ersetzt; die alten Werte bleiben im
        Konfliktdatensatz (``existing``) zur Nachvollziehbarkeit erhalten."""
        ...

    # --- Berichte ----------------------------------------------------------
    def create_report(self, room_id: str, title: str) -> Report: ...
    def get_report(self, report_id: str) -> Report: ...
    def list_reports(self, room_id: str | None = None, include_archived: bool = False) -> list[Report]: ...
    def update_report(self, report_id: str, *, title: str | None = None) -> Report: ...
    def archive_report(self, report_id: str) -> Report: ...
    def delete_report(self, report_id: str) -> None: ...
    def add_report_version(
        self,
        report_id: str,
        *,
        period_start: datetime,
        period_end: datetime,
        config: dict[str, Any],
        content: dict[str, Any],
        data_fingerprint: str,
        make_current: bool = True,
    ) -> ReportVersion:
        """Legt die nächste Versionsnummer an (1, 2, 3 …). Versionen sind unveränderlich."""
        ...
    def list_report_versions(self, report_id: str) -> list[ReportVersion]: ...
    def get_report_version(self, version_id: str) -> ReportVersion: ...
    def set_current_version(self, report_id: str, version_id: str) -> Report: ...

    # --- Freigaben ---------------------------------------------------------
    def create_share(
        self,
        report_id: str,
        *,
        token_hash: str,
        token_hint: str,
        label: str | None = None,
        expires_at: datetime | None = None,
    ) -> Share: ...
    def list_shares(self, report_id: str | None = None) -> list[Share]: ...
    def revoke_share(self, share_id: str) -> Share: ...
    def resolve_share_token(self, token: str) -> dict[str, Any] | None:
        """Öffentlicher Zugriff über den Klartext-Token aus dem Link. Der Token wird erst in
        der Datenbank gehasht (Supabase-RPC ``get_shared_report``), damit ein geleakter Hash
        allein keinen Zugriff erlaubt. Die Oberfläche nutzt dafür
        ``reports.sharing.resolve_shared_report(repo, token)``. Rückgabe wie ``resolve_share``."""
        ...

    def resolve_share(self, token_hash: str) -> dict[str, Any] | None:
        """Nur lokaler Modus/Tests (Supabase wirft RepositoryError). Gibt NUR ``{"content": <snapshot>, "version": int,
        "created_at": iso, "expires_at": iso|None}`` zurück, wenn die Freigabe existiert,
        nicht widerrufen, nicht abgelaufen und der Bericht nicht gelöscht/archiviert ist.
        Sonst ``None``. Keine internen IDs im Ergebnis. Zählt den Zugriff."""
        ...

    # --- Einstellungen -----------------------------------------------------
    def get_settings(self) -> dict[str, Any]: ...
    def save_settings(self, settings: dict[str, Any]) -> None: ...
