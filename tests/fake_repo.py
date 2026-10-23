"""Einfaches In-Memory-Repository für Import-Tests.

Erfüllt das ``Repository``-Protokoll für Räume, Sensoren, Messungen, Importe und Konflikte.
Berichte, Freigaben und Einstellungen werfen ``NotImplementedError``.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime
from typing import Any

import pandas as pd

from climate_dashboard.domain.models import (
    MEASUREMENT_COLUMNS,
    METRICS,
    RAW_TIMESTAMP,
    SENSOR_ID,
    TS,
    ConflictResolution,
    ImportRecord,
    MeasurementConflict,
    Room,
    Sensor,
    new_id,
    utcnow,
)
from climate_dashboard.storage.base import IntegrityError, NotFoundError, RepositoryError


class FakeRepository:
    """In-Memory-Implementierung. ``fail_after_batches=n`` lässt ``insert_measurements`` nach
    ``n`` erfolgreichen Aufrufen mit ``RepositoryError`` scheitern (Rollback-Tests)."""

    def __init__(self, fail_after_batches: int | None = None) -> None:
        self.rooms: dict[str, Room] = {}
        self.sensors: dict[str, Sensor] = {}
        self.imports: dict[str, ImportRecord] = {}
        self.conflicts: dict[str, MeasurementConflict] = {}
        self._rows: dict[tuple[str, int], dict[str, Any]] = {}  # (sensor_id, ts_ns) -> Zeile
        self.fail_after_batches = fail_after_batches
        self.insert_calls = 0
        self.load_calls: list[tuple[list[str], datetime | None, datetime | None]] = []

    # --- Räume -------------------------------------------------------------
    def list_rooms(self, include_archived: bool = False) -> list[Room]:
        return [r for r in self.rooms.values() if include_archived or not r.is_archived]

    def get_room(self, room_id: str) -> Room:
        if room_id not in self.rooms:
            raise NotFoundError(room_id)
        return self.rooms[room_id]

    def create_room(self, name: str, description: str | None = None) -> Room:
        room = Room(id=new_id(), name=name, description=description)
        self.rooms[room.id] = room
        return room

    def rename_room(self, room_id: str, new_name: str) -> Room:
        room = replace(self.get_room(room_id), name=new_name)
        self.rooms[room_id] = room
        return room

    def update_room(self, room_id: str, *, description: str | None = None) -> Room:
        room = replace(self.get_room(room_id), description=description)
        self.rooms[room_id] = room
        return room

    def archive_room(self, room_id: str) -> Room:
        room = replace(self.get_room(room_id), archived_at=utcnow())
        self.rooms[room_id] = room
        return room

    def unarchive_room(self, room_id: str) -> Room:
        room = replace(self.get_room(room_id), archived_at=None)
        self.rooms[room_id] = room
        return room

    def delete_room(self, room_id: str) -> None:
        self.get_room(room_id)
        for s in [s for s in self.sensors.values() if s.room_id == room_id]:
            self._rows = {k: v for k, v in self._rows.items() if k[0] != s.id}
            del self.sensors[s.id]
        del self.rooms[room_id]

    # --- Sensoren ----------------------------------------------------------
    def list_sensors(self, room_id: str | None = None, include_archived: bool = False) -> list[Sensor]:
        return [
            s
            for s in self.sensors.values()
            if (room_id is None or s.room_id == room_id) and (include_archived or not s.is_archived)
        ]

    def get_sensor(self, sensor_id: str) -> Sensor:
        if sensor_id not in self.sensors:
            raise NotFoundError(sensor_id)
        return self.sensors[sensor_id]

    def create_sensor(
        self,
        room_id: str,
        name: str,
        *,
        device_info: str | None = None,
        timezone: str = "Europe/Berlin",
        expected_interval_s: int | None = None,
    ) -> Sensor:
        self.get_room(room_id)
        sensor = Sensor(
            id=new_id(),
            room_id=room_id,
            name=name,
            device_info=device_info,
            timezone=timezone,
            expected_interval_s=expected_interval_s,
        )
        self.sensors[sensor.id] = sensor
        return sensor

    def update_sensor(self, sensor_id: str, **fields: Any) -> Sensor:
        sensor = replace(self.get_sensor(sensor_id), **fields)
        self.sensors[sensor_id] = sensor
        return sensor

    def archive_sensor(self, sensor_id: str) -> Sensor:
        return self.update_sensor(sensor_id, archived_at=utcnow())

    def unarchive_sensor(self, sensor_id: str) -> Sensor:
        return self.update_sensor(sensor_id, archived_at=None)

    # --- Messungen ---------------------------------------------------------
    @staticmethod
    def _ns(value: Any) -> int:
        return int(pd.Timestamp(value).tz_convert("UTC").as_unit("ns").value)

    def load_measurements(
        self, sensor_ids: list[str], start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        self.load_calls.append((list(sensor_ids), start, end))
        lo = self._ns(start) if start is not None else None
        hi = self._ns(end) if end is not None else None
        rows = [
            {SENSOR_ID: sid, TS: ns, **{m: row.get(m) for m in METRICS}}
            for (sid, ns), row in self._rows.items()
            if sid in sensor_ids and (lo is None or ns >= lo) and (hi is None or ns < hi)
        ]
        df = pd.DataFrame(rows, columns=[SENSOR_ID, *MEASUREMENT_COLUMNS])
        df[TS] = pd.to_datetime(df[TS].astype("int64"), unit="ns", utc=True)
        for m in METRICS:
            df[m] = df[m].astype("float64")
        return df.sort_values(TS).reset_index(drop=True)

    def measurement_extent(self, sensor_id: str) -> tuple[datetime | None, datetime | None, int]:
        keys = [ns for sid, ns in self._rows if sid == sensor_id]
        if not keys:
            return None, None, 0
        to_dt = lambda v: pd.Timestamp(v, tz="UTC").to_pydatetime()  # noqa: E731
        return to_dt(min(keys)), to_dt(max(keys)), len(keys)

    def insert_measurements(self, sensor_id: str, import_id: str, df: pd.DataFrame) -> int:
        if self.fail_after_batches is not None and self.insert_calls >= self.fail_after_batches:
            raise RepositoryError("Simulierter Datenbankfehler")
        self.insert_calls += 1
        inserted = 0
        for rec in df.to_dict("records"):
            key = (sensor_id, self._ns(rec[TS]))
            if key in self._rows:
                continue  # ON CONFLICT DO NOTHING
            row = {m: (None if pd.isna(rec.get(m)) else float(rec[m])) for m in METRICS}
            row["import_id"] = import_id
            row[RAW_TIMESTAMP] = rec.get(RAW_TIMESTAMP)
            self._rows[key] = row
            inserted += 1
        return inserted

    def delete_measurements_of_import(self, import_id: str) -> int:
        keys = [k for k, v in self._rows.items() if v["import_id"] == import_id]
        for k in keys:
            del self._rows[k]
        return len(keys)

    def replace_measurement_values(
        self, sensor_id: str, ts: datetime, values: dict[str, float | None]
    ) -> None:
        key = (sensor_id, self._ns(ts))
        if key not in self._rows:
            raise NotFoundError(str(ts))
        self._rows[key].update(values)

    def count_measurements(self, sensor_id: str | None = None) -> int:
        return sum(1 for sid, _ in self._rows if sensor_id is None or sid == sensor_id)

    # --- Importe -----------------------------------------------------------
    def create_import(self, record: ImportRecord) -> ImportRecord:
        if record.id in self.imports:
            raise IntegrityError(record.id)
        self.imports[record.id] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def update_import(self, record: ImportRecord) -> ImportRecord:
        if record.id not in self.imports:
            raise NotFoundError(record.id)
        self.imports[record.id] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def get_import(self, import_id: str) -> ImportRecord:
        if import_id not in self.imports:
            raise NotFoundError(import_id)
        return copy.deepcopy(self.imports[import_id])

    def list_imports(self, room_id: str | None = None, sensor_id: str | None = None) -> list[ImportRecord]:
        return [
            copy.deepcopy(r)
            for r in self.imports.values()
            if (room_id is None or r.room_id == room_id) and (sensor_id is None or r.sensor_id == sensor_id)
        ]

    def find_imports_by_hash(self, sensor_id: str, file_sha256: str) -> list[ImportRecord]:
        return [r for r in self.list_imports(sensor_id=sensor_id) if r.file_sha256 == file_sha256]

    # --- Konflikte ---------------------------------------------------------
    def add_conflicts(self, conflicts: list[MeasurementConflict]) -> None:
        for c in conflicts:
            self.conflicts[c.id] = c

    def delete_conflicts_of_import(self, import_id: str) -> int:
        ids = [k for k, c in self.conflicts.items() if c.import_id == import_id]
        for k in ids:
            del self.conflicts[k]
        return len(ids)

    def list_conflicts(
        self, sensor_id: str | None = None, unresolved_only: bool = True
    ) -> list[MeasurementConflict]:
        return [
            c
            for c in self.conflicts.values()
            if (sensor_id is None or c.sensor_id == sensor_id)
            and (not unresolved_only or c.resolution is None)
        ]

    def resolve_conflict(self, conflict_id: str, resolution: ConflictResolution) -> MeasurementConflict:
        c = self.conflicts[conflict_id]
        if resolution == ConflictResolution.USE_INCOMING:
            self.replace_measurement_values(c.sensor_id, c.ts, c.incoming)
        c = replace(c, resolution=resolution, resolved_at=utcnow())
        self.conflicts[conflict_id] = c
        return c

    # --- Nicht benötigt ----------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def not_implemented(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(f"FakeRepository.{name} ist für Import-Tests nicht implementiert")

        return not_implemented
