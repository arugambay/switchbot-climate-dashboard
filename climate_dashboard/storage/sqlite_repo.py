"""SQLite-Implementierung des ``Repository``-Protokolls (lokaler Modus und Tests).

Eigenschaften
-------------
* Eine Datei (oder ``":memory:"``), ein Nutzer. Kein Login.
* Fremdschlüssel aktiv (``PRAGMA foreign_keys = ON``), WAL-Journal für Dateien.
* Schema-Migrationen sind unten in ``MIGRATIONS`` versioniert; der Stand steht in
  ``PRAGMA user_version``. Neue Migrationen nur anhängen, nie bestehende ändern.
* Messungen: ``ts`` als INTEGER (Unix-Epoch-Sekunden, UTC). Primärschlüssel
  ``(sensor_id, ts)`` (= UNIQUE + Index für Bereichsabfragen, Tabelle ``WITHOUT ROWID``).
  Auflösung ist damit eine Sekunde – SwitchBot-Exporte haben Sekundenauflösung.
* Einfügen per ``INSERT … ON CONFLICT (sensor_id, ts) DO NOTHING`` in einer Transaktion
  (``executemany``); CHECK-Verletzungen brechen den ganzen Batch ab.
* Jede Messung trägt ``import_id`` → Rollback eines Imports per
  ``delete_measurements_of_import``.
* ``delete_room`` kaskadiert über Fremdschlüssel: Sensoren → Messungen, Importprotokolle,
  Konflikte; Berichte → Versionen, Freigaben.
* Raumnamen: Ein *aktiver* Raum darf keinen gleichen normalisierten Namen haben wie ein
  anderer aktiver Raum (Groß/Klein, Leerzeichen egal). Archivierte Räume zählen nicht.
  Umbenennen ändert nur ``name`` – IDs und Verknüpfungen bleiben.
* Berichtsversionen sind unveränderlich (Trigger verbietet UPDATE).
* Freigaben speichern nur ``token_hash`` (SHA-256 hex), niemals den Klartext-Token.

Thread-Sicherheit: Streamlit ruft aus mehreren Threads auf; alle Zugriffe laufen über eine
Verbindung, die mit einem Lock geschützt ist.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import (
    MEASUREMENT_COLUMNS,
    METRICS,
    RAW_TIMESTAMP,
    SENSOR_ID,
    TS,
    ConflictResolution,
    ImportRecord,
    ImportStatus,
    MeasurementConflict,
    Report,
    ReportVersion,
    Room,
    Sensor,
    Share,
    new_id,
    utcnow,
)
from climate_dashboard.storage.base import IntegrityError, NotFoundError, RepositoryError

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA_V1 = """
CREATE TABLE rooms (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    name_norm    TEXT NOT NULL,
    description  TEXT,
    created_at   TEXT NOT NULL,
    archived_at  TEXT
);
CREATE INDEX rooms_name_norm_idx ON rooms (name_norm);

CREATE TABLE sensors (
    id                   TEXT PRIMARY KEY,
    room_id              TEXT NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    name                 TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    device_info          TEXT,
    timezone             TEXT NOT NULL DEFAULT 'Europe/Berlin',
    expected_interval_s  INTEGER CHECK (expected_interval_s IS NULL OR expected_interval_s > 0),
    created_at           TEXT NOT NULL,
    archived_at          TEXT
);
CREATE INDEX sensors_room_idx ON sensors (room_id);

CREATE TABLE imports (
    id                   TEXT PRIMARY KEY,
    sensor_id            TEXT NOT NULL REFERENCES sensors (id) ON DELETE CASCADE,
    room_id              TEXT NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    file_name            TEXT NOT NULL,
    file_sha256          TEXT NOT NULL,
    status               TEXT NOT NULL
                         CHECK (status IN ('pending', 'completed', 'failed', 'rolled_back')),
    rows_read            INTEGER NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    rows_new             INTEGER NOT NULL DEFAULT 0 CHECK (rows_new >= 0),
    rows_duplicate       INTEGER NOT NULL DEFAULT 0 CHECK (rows_duplicate >= 0),
    rows_invalid         INTEGER NOT NULL DEFAULT 0 CHECK (rows_invalid >= 0),
    rows_conflict        INTEGER NOT NULL DEFAULT 0 CHECK (rows_conflict >= 0),
    ts_min               TEXT,
    ts_max               TEXT,
    expected_interval_s  INTEGER,
    observed_interval_s  INTEGER,
    warnings             TEXT NOT NULL DEFAULT '[]',
    details              TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    finished_at          TEXT
);
CREATE INDEX imports_sensor_idx ON imports (sensor_id, file_sha256);
CREATE INDEX imports_room_idx ON imports (room_id);

CREATE TABLE measurements (
    sensor_id      TEXT NOT NULL REFERENCES sensors (id) ON DELETE CASCADE,
    ts             INTEGER NOT NULL,           -- Unix-Epoch-Sekunden UTC
    temperature    REAL CHECK (temperature IS NULL OR temperature BETWEEN -100 AND 100),
    humidity       REAL CHECK (humidity IS NULL OR humidity BETWEEN 0 AND 100),
    dew_point      REAL CHECK (dew_point IS NULL OR dew_point BETWEEN -100 AND 100),
    vpd            REAL CHECK (vpd IS NULL OR vpd >= 0),
    abs_humidity   REAL CHECK (abs_humidity IS NULL OR abs_humidity >= 0),
    raw_timestamp  TEXT,                       -- Originaltext aus der CSV
    import_id      TEXT REFERENCES imports (id) ON DELETE SET NULL,
    PRIMARY KEY (sensor_id, ts)                -- = UNIQUE(sensor_id, ts) + Bereichsindex
) WITHOUT ROWID;
CREATE INDEX measurements_import_idx ON measurements (import_id);

CREATE TABLE measurement_conflicts (
    id           TEXT PRIMARY KEY,
    sensor_id    TEXT NOT NULL REFERENCES sensors (id) ON DELETE CASCADE,
    import_id    TEXT NOT NULL REFERENCES imports (id) ON DELETE CASCADE,
    ts           INTEGER NOT NULL,
    existing     TEXT NOT NULL,
    incoming     TEXT NOT NULL,
    resolution   TEXT CHECK (resolution IS NULL OR resolution IN ('keep_existing', 'use_incoming')),
    resolved_at  TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX conflicts_sensor_idx ON measurement_conflicts (sensor_id, resolution);

CREATE TABLE reports (
    id                  TEXT PRIMARY KEY,
    room_id             TEXT NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    title               TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    current_version_id  TEXT REFERENCES report_versions (id) ON DELETE SET NULL
                        DEFERRABLE INITIALLY DEFERRED,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    archived_at         TEXT
);
CREATE INDEX reports_room_idx ON reports (room_id);

CREATE TABLE report_versions (
    id                TEXT PRIMARY KEY,
    report_id         TEXT NOT NULL REFERENCES reports (id) ON DELETE CASCADE,
    version           INTEGER NOT NULL CHECK (version >= 1),
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    config            TEXT NOT NULL,
    content           TEXT NOT NULL,
    data_fingerprint  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (report_id, version)
);

-- Versionen sind unveränderlich.
CREATE TRIGGER report_versions_immutable
BEFORE UPDATE ON report_versions
BEGIN
    SELECT RAISE(ABORT, 'report_versions sind unveränderlich');
END;

CREATE TABLE shares (
    id                TEXT PRIMARY KEY,
    report_id         TEXT NOT NULL REFERENCES reports (id) ON DELETE CASCADE,
    token_hash        TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    token_hint        TEXT NOT NULL CHECK (length(token_hint) <= 8),
    label             TEXT,
    created_at        TEXT NOT NULL,
    expires_at        TEXT,
    revoked_at        TEXT,
    last_accessed_at  TEXT,
    access_count      INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0)
);
CREATE INDEX shares_report_idx ON shares (report_id);

CREATE TABLE user_settings (
    id          INTEGER PRIMARY KEY CHECK (id = 1),   -- Einzelnutzer
    settings    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

# (Version, SQL). Nur anhängen!
MIGRATIONS: list[tuple[int, str]] = [(1, _SCHEMA_V1)]
SCHEMA_VERSION = MIGRATIONS[-1][0]

_SENSOR_FIELDS = {"name", "device_info", "timezone", "expected_interval_s", "room_id"}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def normalize_room_name(name: str) -> str:
    """Vergleichsform eines Raumnamens: getrimmt, Mehrfach-Leerzeichen → eins, casefold."""
    return re.sub(r"\s+", " ", name.strip()).casefold()


def _clean_name(name: str, what: str = "Name") -> str:
    if not isinstance(name, str) or not name.strip():
        raise IntegrityError(f"{what} darf nicht leer sein.")
    return re.sub(r"\s+", " ", name.strip())


def _dt_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise RepositoryError("Zeitangaben müssen eine Zeitzone haben (tz-aware, UTC).")
    return value.astimezone(UTC).isoformat()


def _text_to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _dt_to_epoch(value: datetime) -> int:
    if value.tzinfo is None:
        raise RepositoryError("Zeitangaben müssen eine Zeitzone haben (tz-aware, UTC).")
    return math.floor(value.timestamp())


def _ceil_epoch(value: datetime) -> int:
    if value.tzinfo is None:
        raise RepositoryError("Zeitangaben müssen eine Zeitzone haben (tz-aware, UTC).")
    return math.ceil(value.timestamp())


def _raw_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _epoch_to_dt(value: int) -> datetime:
    return datetime.fromtimestamp(int(value), tz=UTC)


def _json_dumps(value: Any) -> str:
    try:
        # allow_nan=False: identisches Verhalten wie PostgreSQL jsonb (kein NaN erlaubt)
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(f"Daten sind nicht als JSON speicherbar: {exc}") from exc


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _ts_series_to_epoch(series: pd.Series) -> np.ndarray:
    ts = pd.to_datetime(series)
    if getattr(ts.dt, "tz", None) is None:
        raise RepositoryError("Spalte 'ts' muss tz-aware (UTC) sein.")
    ts = ts.dt.tz_convert("UTC")
    epoch = pd.Timestamp(0, tz="UTC")
    return ((ts - epoch) // pd.Timedelta(seconds=1)).to_numpy(dtype="int64")


def _empty_measurements(include_raw: bool = False) -> pd.DataFrame:
    data: dict[str, Any] = {
        SENSOR_ID: pd.Series(dtype="object"),
        TS: pd.Series(dtype="datetime64[ns, UTC]"),
    }
    for m in METRICS:
        data[m] = pd.Series(dtype="float64")
    if include_raw:
        data[RAW_TIMESTAMP] = pd.Series(dtype="object")
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
class SqliteRepository:
    """Lokale Speicherung in SQLite. ``path`` = Dateipfad oder ``":memory:"``."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as exc:
            raise RepositoryError(f"SQLite-Datenbank konnte nicht geöffnet werden: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    # --- Infrastruktur -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                # executescript: ganze Migration + Versionsnummer in EINER Transaktion
                script = f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {int(version)};\nCOMMIT;"
                try:
                    self._conn.executescript(script)
                except sqlite3.Error as exc:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise RepositoryError(f"Schema-Migration {version} fehlgeschlagen: {exc}") from exc

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Schreibtransaktion; übersetzt SQLite-Fehler in Repository-Fehler."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise RepositoryError(f"Datenbank nicht verfügbar: {exc}") from exc
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException as exc:
                self._conn.execute("ROLLBACK")
                if isinstance(exc, sqlite3.IntegrityError):
                    raise IntegrityError(_integrity_message(exc)) from exc
                if isinstance(exc, sqlite3.Error):
                    raise RepositoryError(f"Datenbankfehler: {exc}") from exc
                raise

    def _query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise RepositoryError(f"Datenbankfehler: {exc}") from exc

    def _one(self, sql: str, params: tuple | list, not_found: str) -> sqlite3.Row:
        rows = self._query(sql, params)
        if not rows:
            raise NotFoundError(not_found)
        return rows[0]

    # --- Räume -----------------------------------------------------------------
    @staticmethod
    def _room(row: sqlite3.Row) -> Room:
        return Room(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=_text_to_dt(row["created_at"]),
            archived_at=_text_to_dt(row["archived_at"]),
        )

    def _assert_name_free(self, conn: sqlite3.Connection, name_norm: str, exclude_id: str | None) -> None:
        row = conn.execute(
            "SELECT id FROM rooms WHERE name_norm = ? AND archived_at IS NULL AND id IS NOT ?",
            (name_norm, exclude_id),
        ).fetchone()
        if row is not None:
            raise IntegrityError("Es gibt bereits einen aktiven Raum mit diesem Namen.")

    def list_rooms(self, include_archived: bool = False) -> list[Room]:
        sql = "SELECT * FROM rooms"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY name_norm, created_at"
        return [self._room(r) for r in self._query(sql)]

    def get_room(self, room_id: str) -> Room:
        return self._room(self._one("SELECT * FROM rooms WHERE id = ?", (room_id,), "Raum nicht gefunden."))

    def create_room(self, name: str, description: str | None = None) -> Room:
        clean = _clean_name(name, "Raumname")
        room = Room(id=new_id(), name=clean, description=description)
        with self._tx() as conn:
            self._assert_name_free(conn, normalize_room_name(clean), None)
            conn.execute(
                "INSERT INTO rooms (id, name, name_norm, description, created_at) VALUES (?, ?, ?, ?, ?)",
                (room.id, room.name, normalize_room_name(clean), description, _dt_to_text(room.created_at)),
            )
        return room

    def rename_room(self, room_id: str, new_name: str) -> Room:
        clean = _clean_name(new_name, "Raumname")
        with self._tx() as conn:
            row = conn.execute("SELECT archived_at FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if row is None:
                raise NotFoundError("Raum nicht gefunden.")
            if row["archived_at"] is None:
                self._assert_name_free(conn, normalize_room_name(clean), room_id)
            conn.execute(
                "UPDATE rooms SET name = ?, name_norm = ? WHERE id = ?",
                (clean, normalize_room_name(clean), room_id),
            )
        return self.get_room(room_id)

    def update_room(self, room_id: str, *, description: str | None = None) -> Room:
        with self._tx() as conn:
            cur = conn.execute("UPDATE rooms SET description = ? WHERE id = ?", (description, room_id))
            if cur.rowcount == 0:
                raise NotFoundError("Raum nicht gefunden.")
        return self.get_room(room_id)

    def archive_room(self, room_id: str) -> Room:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE rooms SET archived_at = COALESCE(archived_at, ?) WHERE id = ?",
                (_dt_to_text(utcnow()), room_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Raum nicht gefunden.")
        return self.get_room(room_id)

    def unarchive_room(self, room_id: str) -> Room:
        with self._tx() as conn:
            row = conn.execute("SELECT name_norm FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if row is None:
                raise NotFoundError("Raum nicht gefunden.")
            self._assert_name_free(conn, row["name_norm"], room_id)
            conn.execute("UPDATE rooms SET archived_at = NULL WHERE id = ?", (room_id,))
        return self.get_room(room_id)

    def delete_room(self, room_id: str) -> None:
        """Kaskadiert per Fremdschlüssel: Sensoren, Messungen, Importe, Konflikte, Berichte,
        Versionen und Freigaben des Raums werden mitgelöscht."""
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
            if cur.rowcount == 0:
                raise NotFoundError("Raum nicht gefunden.")

    # --- Sensoren --------------------------------------------------------------
    @staticmethod
    def _sensor(row: sqlite3.Row) -> Sensor:
        return Sensor(
            id=row["id"],
            room_id=row["room_id"],
            name=row["name"],
            device_info=row["device_info"],
            timezone=row["timezone"],
            expected_interval_s=row["expected_interval_s"],
            created_at=_text_to_dt(row["created_at"]),
            archived_at=_text_to_dt(row["archived_at"]),
        )

    def list_sensors(self, room_id: str | None = None, include_archived: bool = False) -> list[Sensor]:
        clauses, params = [], []
        if room_id is not None:
            clauses.append("room_id = ?")
            params.append(room_id)
        if not include_archived:
            clauses.append("archived_at IS NULL")
        sql = "SELECT * FROM sensors"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY name COLLATE NOCASE, created_at"
        return [self._sensor(r) for r in self._query(sql, params)]

    def get_sensor(self, sensor_id: str) -> Sensor:
        return self._sensor(
            self._one("SELECT * FROM sensors WHERE id = ?", (sensor_id,), "Sensor nicht gefunden.")
        )

    def create_sensor(
        self,
        room_id: str,
        name: str,
        *,
        device_info: str | None = None,
        timezone: str = "Europe/Berlin",
        expected_interval_s: int | None = None,
    ) -> Sensor:
        sensor = Sensor(
            id=new_id(),
            room_id=room_id,
            name=_clean_name(name, "Sensorname"),
            device_info=device_info,
            timezone=timezone,
            expected_interval_s=expected_interval_s,
        )
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE id = ?", (room_id,)).fetchone() is None:
                raise NotFoundError("Raum nicht gefunden.")
            conn.execute(
                "INSERT INTO sensors (id, room_id, name, device_info, timezone, expected_interval_s, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sensor.id,
                    room_id,
                    sensor.name,
                    device_info,
                    timezone,
                    expected_interval_s,
                    _dt_to_text(sensor.created_at),
                ),
            )
        return sensor

    def update_sensor(self, sensor_id: str, **fields: Any) -> Sensor:
        unknown = set(fields) - _SENSOR_FIELDS
        if unknown:
            raise RepositoryError(f"Nicht änderbare Sensorfelder: {', '.join(sorted(unknown))}")
        if "name" in fields:
            fields["name"] = _clean_name(fields["name"], "Sensorname")
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM sensors WHERE id = ?", (sensor_id,)).fetchone() is None:
                raise NotFoundError("Sensor nicht gefunden.")
            if "room_id" in fields and (
                conn.execute("SELECT 1 FROM rooms WHERE id = ?", (fields["room_id"],)).fetchone() is None
            ):
                raise NotFoundError("Ziel-Raum nicht gefunden.")
            if fields:
                cols = sorted(fields)
                conn.execute(
                    f"UPDATE sensors SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                    [fields[c] for c in cols] + [sensor_id],
                )
            if "room_id" in fields:
                # Importprotokolle wandern mit dem Sensor in den neuen Raum.
                conn.execute("UPDATE imports SET room_id = ? WHERE sensor_id = ?", (fields["room_id"], sensor_id))
        return self.get_sensor(sensor_id)

    def archive_sensor(self, sensor_id: str) -> Sensor:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE sensors SET archived_at = COALESCE(archived_at, ?) WHERE id = ?",
                (_dt_to_text(utcnow()), sensor_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Sensor nicht gefunden.")
        return self.get_sensor(sensor_id)

    def unarchive_sensor(self, sensor_id: str) -> Sensor:
        with self._tx() as conn:
            cur = conn.execute("UPDATE sensors SET archived_at = NULL WHERE id = ?", (sensor_id,))
            if cur.rowcount == 0:
                raise NotFoundError("Sensor nicht gefunden.")
        return self.get_sensor(sensor_id)

    # --- Messungen -------------------------------------------------------------
    def load_measurements(
        self,
        sensor_ids: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        include_raw: bool = False,
    ) -> pd.DataFrame:
        """[start, end), sortiert nach ``ts``. ``include_raw`` ergänzt ``raw_timestamp``."""
        if not sensor_ids:
            return _empty_measurements(include_raw)
        cols = ["sensor_id", "ts", *METRICS] + (["raw_timestamp"] if include_raw else [])
        sql = f"SELECT {', '.join(cols)} FROM measurements WHERE sensor_id IN ({', '.join('?' * len(sensor_ids))})"
        params: list[Any] = list(sensor_ids)
        if start is not None:
            sql += " AND ts >= ?"
            params.append(_ceil_epoch(start))
        if end is not None:
            # [start, end) bei ganzzahligen Sekunden: ceil(start) <= ts < ceil(end)
            sql += " AND ts < ?"
            params.append(_ceil_epoch(end))
        sql += " ORDER BY ts, sensor_id"
        with self._lock:
            try:
                df = pd.read_sql_query(sql, self._conn, params=params)
            except (sqlite3.Error, pd.errors.DatabaseError) as exc:
                raise RepositoryError(f"Messungen konnten nicht geladen werden: {exc}") from exc
        if df.empty:
            return _empty_measurements(include_raw)
        df[TS] = pd.to_datetime(df[TS].astype("int64"), unit="s", utc=True).astype("datetime64[ns, UTC]")
        for m in METRICS:
            df[m] = df[m].astype("float64")
        df[SENSOR_ID] = df[SENSOR_ID].astype("object")
        if include_raw:
            raw = df[RAW_TIMESTAMP].astype("object")
            df[RAW_TIMESTAMP] = raw.where(raw.notna(), None)
        return df.reset_index(drop=True)

    def measurement_extent(self, sensor_id: str) -> tuple[datetime | None, datetime | None, int]:
        row = self._query(
            "SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n FROM measurements WHERE sensor_id = ?",
            (sensor_id,),
        )[0]
        if not row["n"]:
            return None, None, 0
        return _epoch_to_dt(row["first"]), _epoch_to_dt(row["last"]), int(row["n"])

    def insert_measurements(self, sensor_id: str, import_id: str, df: pd.DataFrame) -> int:
        if df is None or len(df) == 0:
            return 0
        missing = [c for c in MEASUREMENT_COLUMNS if c not in df.columns]
        if missing:
            raise RepositoryError(f"Fehlende Spalten: {', '.join(missing)}")
        epochs = _ts_series_to_epoch(df[TS])
        metric_values = [df[m].to_numpy(dtype="float64", na_value=np.nan) for m in METRICS]
        raw = df[RAW_TIMESTAMP].tolist() if RAW_TIMESTAMP in df.columns else [None] * len(df)
        rows = [
            (
                sensor_id,
                int(epochs[i]),
                *(_float_or_none(col[i]) for col in metric_values),
                _raw_or_none(raw[i]),
                import_id,
            )
            for i in range(len(df))
        ]
        with self._tx() as conn:
            before = conn.total_changes
            # ON CONFLICT DO NOTHING statt INSERT OR IGNORE: ignoriert nur doppelte
            # (sensor_id, ts). INSERT OR IGNORE würde auch CHECK-Verletzungen still verwerfen.
            conn.executemany(
                "INSERT INTO measurements"
                " (sensor_id, ts, temperature, humidity, dew_point, vpd, abs_humidity, raw_timestamp, import_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (sensor_id, ts) DO NOTHING",
                rows,
            )
            return conn.total_changes - before

    def delete_measurements_of_import(self, import_id: str) -> int:
        with self._tx() as conn:
            return conn.execute("DELETE FROM measurements WHERE import_id = ?", (import_id,)).rowcount

    def replace_measurement_values(
        self, sensor_id: str, ts: datetime, values: dict[str, float | None]
    ) -> None:
        with self._tx() as conn:
            self._replace_values(conn, sensor_id, _dt_to_epoch(ts), values)

    @staticmethod
    def _replace_values(conn: sqlite3.Connection, sensor_id: str, epoch: int, values: dict) -> None:
        unknown = set(values) - set(METRICS)
        if unknown:
            raise RepositoryError(f"Unbekannte Messgrößen: {', '.join(sorted(unknown))}")
        if not values:
            return
        cols = [m for m in METRICS if m in values]
        cur = conn.execute(
            f"UPDATE measurements SET {', '.join(f'{c} = ?' for c in cols)} WHERE sensor_id = ? AND ts = ?",
            [_float_or_none(values[c]) for c in cols] + [sensor_id, epoch],
        )
        if cur.rowcount == 0:
            raise NotFoundError("Messung zu diesem Zeitpunkt nicht gefunden.")

    # --- Importe ---------------------------------------------------------------
    _IMPORT_COLS = (
        "id, sensor_id, room_id, file_name, file_sha256, status, rows_read, rows_new, rows_duplicate,"
        " rows_invalid, rows_conflict, ts_min, ts_max, expected_interval_s, observed_interval_s,"
        " warnings, details, created_at, finished_at"
    )

    @staticmethod
    def _import_params(r: ImportRecord) -> tuple:
        return (
            r.id,
            r.sensor_id,
            r.room_id,
            r.file_name,
            r.file_sha256,
            ImportStatus(r.status).value,
            r.rows_read,
            r.rows_new,
            r.rows_duplicate,
            r.rows_invalid,
            r.rows_conflict,
            _dt_to_text(r.ts_min),
            _dt_to_text(r.ts_max),
            r.expected_interval_s,
            r.observed_interval_s,
            _json_dumps(list(r.warnings)),
            _json_dumps(dict(r.details)),
            _dt_to_text(r.created_at),
            _dt_to_text(r.finished_at),
        )

    @staticmethod
    def _import(row: sqlite3.Row) -> ImportRecord:
        return ImportRecord(
            id=row["id"],
            sensor_id=row["sensor_id"],
            room_id=row["room_id"],
            file_name=row["file_name"],
            file_sha256=row["file_sha256"],
            status=ImportStatus(row["status"]),
            rows_read=row["rows_read"],
            rows_new=row["rows_new"],
            rows_duplicate=row["rows_duplicate"],
            rows_invalid=row["rows_invalid"],
            rows_conflict=row["rows_conflict"],
            ts_min=_text_to_dt(row["ts_min"]),
            ts_max=_text_to_dt(row["ts_max"]),
            expected_interval_s=row["expected_interval_s"],
            observed_interval_s=row["observed_interval_s"],
            warnings=json.loads(row["warnings"]),
            details=json.loads(row["details"]),
            created_at=_text_to_dt(row["created_at"]),
            finished_at=_text_to_dt(row["finished_at"]),
        )

    def create_import(self, record: ImportRecord) -> ImportRecord:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM sensors WHERE id = ?", (record.sensor_id,)).fetchone() is None:
                raise NotFoundError("Sensor nicht gefunden.")
            conn.execute(
                f"INSERT INTO imports ({self._IMPORT_COLS}) VALUES ({', '.join('?' * 19)})",
                self._import_params(record),
            )
        return self.get_import(record.id)

    def update_import(self, record: ImportRecord) -> ImportRecord:
        params = self._import_params(record)
        cols = [c.strip() for c in self._IMPORT_COLS.split(",")]
        # id, sensor_id, room_id, created_at bleiben unverändert
        fixed = {"id", "sensor_id", "room_id", "created_at"}
        updates = [(c, p) for c, p in zip(cols, params, strict=True) if c not in fixed]
        with self._tx() as conn:
            cur = conn.execute(
                f"UPDATE imports SET {', '.join(f'{c} = ?' for c, _ in updates)} WHERE id = ?",
                [p for _, p in updates] + [record.id],
            )
            if cur.rowcount == 0:
                raise NotFoundError("Import nicht gefunden.")
        return self.get_import(record.id)

    def get_import(self, import_id: str) -> ImportRecord:
        return self._import(
            self._one(
                f"SELECT {self._IMPORT_COLS} FROM imports WHERE id = ?",
                (import_id,),
                "Import nicht gefunden.",
            )
        )

    def list_imports(self, room_id: str | None = None, sensor_id: str | None = None) -> list[ImportRecord]:
        clauses, params = [], []
        if room_id is not None:
            clauses.append("room_id = ?")
            params.append(room_id)
        if sensor_id is not None:
            clauses.append("sensor_id = ?")
            params.append(sensor_id)
        sql = f"SELECT {self._IMPORT_COLS} FROM imports"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        return [self._import(r) for r in self._query(sql, params)]

    def find_imports_by_hash(self, sensor_id: str, file_sha256: str) -> list[ImportRecord]:
        rows = self._query(
            f"SELECT {self._IMPORT_COLS} FROM imports WHERE sensor_id = ? AND file_sha256 = ?"
            " ORDER BY created_at DESC",
            (sensor_id, file_sha256),
        )
        return [self._import(r) for r in rows]

    # --- Konflikte -------------------------------------------------------------
    @staticmethod
    def _conflict(row: sqlite3.Row) -> MeasurementConflict:
        return MeasurementConflict(
            id=row["id"],
            sensor_id=row["sensor_id"],
            import_id=row["import_id"],
            ts=_epoch_to_dt(row["ts"]),
            existing=json.loads(row["existing"]),
            incoming=json.loads(row["incoming"]),
            resolution=ConflictResolution(row["resolution"]) if row["resolution"] else None,
            resolved_at=_text_to_dt(row["resolved_at"]),
            created_at=_text_to_dt(row["created_at"]),
        )

    @staticmethod
    def _clean_values(values: dict[str, float | None]) -> dict[str, float | None]:
        return {k: _float_or_none(v) for k, v in values.items()}

    def add_conflicts(self, conflicts: list[MeasurementConflict]) -> None:
        if not conflicts:
            return
        rows = [
            (
                c.id,
                c.sensor_id,
                c.import_id,
                _dt_to_epoch(c.ts),
                _json_dumps(self._clean_values(c.existing)),
                _json_dumps(self._clean_values(c.incoming)),
                ConflictResolution(c.resolution).value if c.resolution else None,
                _dt_to_text(c.resolved_at),
                _dt_to_text(c.created_at),
            )
            for c in conflicts
        ]
        with self._tx() as conn:
            conn.executemany(
                "INSERT INTO measurement_conflicts"
                " (id, sensor_id, import_id, ts, existing, incoming, resolution, resolved_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def delete_conflicts_of_import(self, import_id: str) -> int:
        with self._tx() as conn:
            return conn.execute("DELETE FROM measurement_conflicts WHERE import_id = ?", (import_id,)).rowcount

    def list_conflicts(
        self, sensor_id: str | None = None, unresolved_only: bool = True
    ) -> list[MeasurementConflict]:
        clauses, params = [], []
        if sensor_id is not None:
            clauses.append("sensor_id = ?")
            params.append(sensor_id)
        if unresolved_only:
            clauses.append("resolution IS NULL")
        sql = "SELECT * FROM measurement_conflicts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts, created_at"
        return [self._conflict(r) for r in self._query(sql, params)]

    def resolve_conflict(self, conflict_id: str, resolution: ConflictResolution) -> MeasurementConflict:
        """USE_INCOMING ersetzt die Messwerte (atomar mit dem Konfliktstatus); ``existing``
        bleibt im Konfliktdatensatz erhalten. Bereits aufgelöste Konflikte → IntegrityError."""
        resolution = ConflictResolution(resolution)
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM measurement_conflicts WHERE id = ?", (conflict_id,)).fetchone()
            if row is None:
                raise NotFoundError("Konflikt nicht gefunden.")
            if row["resolution"] is not None:
                raise IntegrityError("Dieser Konflikt wurde bereits aufgelöst.")
            if resolution is ConflictResolution.USE_INCOMING:
                incoming = {k: v for k, v in json.loads(row["incoming"]).items() if k in METRICS}
                self._replace_values(conn, row["sensor_id"], row["ts"], incoming)
            conn.execute(
                "UPDATE measurement_conflicts SET resolution = ?, resolved_at = ? WHERE id = ?",
                (resolution.value, _dt_to_text(utcnow()), conflict_id),
            )
        return self._conflict(
            self._one(
                "SELECT * FROM measurement_conflicts WHERE id = ?", (conflict_id,), "Konflikt nicht gefunden."
            )
        )

    # --- Berichte --------------------------------------------------------------
    @staticmethod
    def _report(row: sqlite3.Row) -> Report:
        return Report(
            id=row["id"],
            room_id=row["room_id"],
            title=row["title"],
            current_version_id=row["current_version_id"],
            created_at=_text_to_dt(row["created_at"]),
            updated_at=_text_to_dt(row["updated_at"]),
            archived_at=_text_to_dt(row["archived_at"]),
        )

    @staticmethod
    def _version(row: sqlite3.Row) -> ReportVersion:
        return ReportVersion(
            id=row["id"],
            report_id=row["report_id"],
            version=row["version"],
            period_start=_text_to_dt(row["period_start"]),
            period_end=_text_to_dt(row["period_end"]),
            config=json.loads(row["config"]),
            content=json.loads(row["content"]),
            data_fingerprint=row["data_fingerprint"],
            created_at=_text_to_dt(row["created_at"]),
        )

    def create_report(self, room_id: str, title: str) -> Report:
        report = Report(id=new_id(), room_id=room_id, title=_clean_name(title, "Titel"))
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE id = ?", (room_id,)).fetchone() is None:
                raise NotFoundError("Raum nicht gefunden.")
            conn.execute(
                "INSERT INTO reports (id, room_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    report.id,
                    room_id,
                    report.title,
                    _dt_to_text(report.created_at),
                    _dt_to_text(report.updated_at),
                ),
            )
        return report

    def get_report(self, report_id: str) -> Report:
        return self._report(
            self._one("SELECT * FROM reports WHERE id = ?", (report_id,), "Bericht nicht gefunden.")
        )

    def list_reports(self, room_id: str | None = None, include_archived: bool = False) -> list[Report]:
        clauses, params = [], []
        if room_id is not None:
            clauses.append("room_id = ?")
            params.append(room_id)
        if not include_archived:
            clauses.append("archived_at IS NULL")
        sql = "SELECT * FROM reports"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        return [self._report(r) for r in self._query(sql, params)]

    def update_report(self, report_id: str, *, title: str | None = None) -> Report:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM reports WHERE id = ?", (report_id,)).fetchone() is None:
                raise NotFoundError("Bericht nicht gefunden.")
            if title is not None:
                conn.execute(
                    "UPDATE reports SET title = ?, updated_at = ? WHERE id = ?",
                    (_clean_name(title, "Titel"), _dt_to_text(utcnow()), report_id),
                )
        return self.get_report(report_id)

    def archive_report(self, report_id: str) -> Report:
        now = _dt_to_text(utcnow())
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE reports SET archived_at = COALESCE(archived_at, ?), updated_at = ? WHERE id = ?",
                (now, now, report_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Bericht nicht gefunden.")
        return self.get_report(report_id)

    def delete_report(self, report_id: str) -> None:
        """Löscht Bericht inkl. aller Versionen und Freigaben (Kaskade)."""
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            if cur.rowcount == 0:
                raise NotFoundError("Bericht nicht gefunden.")

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
        config_json, content_json = _json_dumps(config), _json_dumps(content)
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM reports WHERE id = ?", (report_id,)).fetchone() is None:
                raise NotFoundError("Bericht nicht gefunden.")
            number = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM report_versions WHERE report_id = ?", (report_id,)
            ).fetchone()[0]
            version = ReportVersion(
                id=new_id(),
                report_id=report_id,
                version=int(number),
                period_start=period_start,
                period_end=period_end,
                config=json.loads(config_json),
                content=json.loads(content_json),
                data_fingerprint=data_fingerprint,
            )
            conn.execute(
                "INSERT INTO report_versions (id, report_id, version, period_start, period_end, config, content,"
                " data_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version.id,
                    report_id,
                    version.version,
                    _dt_to_text(period_start),
                    _dt_to_text(period_end),
                    config_json,
                    content_json,
                    data_fingerprint,
                    _dt_to_text(version.created_at),
                ),
            )
            if make_current:
                conn.execute(
                    "UPDATE reports SET current_version_id = ?, updated_at = ? WHERE id = ?",
                    (version.id, _dt_to_text(utcnow()), report_id),
                )
        return version

    def list_report_versions(self, report_id: str) -> list[ReportVersion]:
        rows = self._query("SELECT * FROM report_versions WHERE report_id = ? ORDER BY version", (report_id,))
        return [self._version(r) for r in rows]

    def get_report_version(self, version_id: str) -> ReportVersion:
        return self._version(
            self._one(
                "SELECT * FROM report_versions WHERE id = ?", (version_id,), "Berichtsversion nicht gefunden."
            )
        )

    def set_current_version(self, report_id: str, version_id: str) -> Report:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM reports WHERE id = ?", (report_id,)).fetchone() is None:
                raise NotFoundError("Bericht nicht gefunden.")
            row = conn.execute(
                "SELECT 1 FROM report_versions WHERE id = ? AND report_id = ?", (version_id, report_id)
            ).fetchone()
            if row is None:
                raise NotFoundError("Version gehört nicht zu diesem Bericht.")
            conn.execute(
                "UPDATE reports SET current_version_id = ?, updated_at = ? WHERE id = ?",
                (version_id, _dt_to_text(utcnow()), report_id),
            )
        return self.get_report(report_id)

    # --- Freigaben -------------------------------------------------------------
    @staticmethod
    def _share(row: sqlite3.Row) -> Share:
        return Share(
            id=row["id"],
            report_id=row["report_id"],
            token_hash=row["token_hash"],
            token_hint=row["token_hint"],
            label=row["label"],
            created_at=_text_to_dt(row["created_at"]),
            expires_at=_text_to_dt(row["expires_at"]),
            revoked_at=_text_to_dt(row["revoked_at"]),
            last_accessed_at=_text_to_dt(row["last_accessed_at"]),
            access_count=row["access_count"],
        )

    def create_share(
        self,
        report_id: str,
        *,
        token_hash: str,
        token_hint: str,
        label: str | None = None,
        expires_at: datetime | None = None,
    ) -> Share:
        if not isinstance(token_hash, str) or not _HASH_RE.match(token_hash):
            raise IntegrityError("token_hash muss ein SHA-256-Hex-Wert sein (niemals der Klartext-Token).")
        share = Share(
            id=new_id(),
            report_id=report_id,
            token_hash=token_hash,
            token_hint=token_hint,
            label=label,
            expires_at=expires_at,
        )
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM reports WHERE id = ?", (report_id,)).fetchone() is None:
                raise NotFoundError("Bericht nicht gefunden.")
            conn.execute(
                "INSERT INTO shares (id, report_id, token_hash, token_hint, label, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    share.id,
                    report_id,
                    token_hash,
                    token_hint,
                    label,
                    _dt_to_text(share.created_at),
                    _dt_to_text(expires_at),
                ),
            )
        return share

    def list_shares(self, report_id: str | None = None) -> list[Share]:
        if report_id is None:
            rows = self._query("SELECT * FROM shares ORDER BY created_at DESC")
        else:
            rows = self._query(
                "SELECT * FROM shares WHERE report_id = ? ORDER BY created_at DESC", (report_id,)
            )
        return [self._share(r) for r in rows]

    def revoke_share(self, share_id: str) -> Share:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE shares SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (_dt_to_text(utcnow()), share_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Freigabe nicht gefunden.")
        return self._share(
            self._one("SELECT * FROM shares WHERE id = ?", (share_id,), "Freigabe nicht gefunden.")
        )

    def resolve_share(self, token_hash: str) -> dict[str, Any] | None:
        if not isinstance(token_hash, str) or not _HASH_RE.match(token_hash):
            return None
        now = utcnow()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT s.id AS share_id, s.revoked_at, s.expires_at, r.archived_at AS report_archived,"
                "       v.content, v.version, v.created_at AS version_created"
                "  FROM shares s"
                "  JOIN reports r ON r.id = s.report_id"
                "  JOIN report_versions v ON v.id = r.current_version_id AND v.report_id = r.id"
                " WHERE s.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or row["report_archived"] is not None:
                return None
            expires = _text_to_dt(row["expires_at"])
            if expires is not None and expires <= now:
                return None
            conn.execute(
                "UPDATE shares SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
                (_dt_to_text(now), row["share_id"]),
            )
            return {
                "content": json.loads(row["content"]),
                "version": int(row["version"]),
                "created_at": row["version_created"],
                "expires_at": row["expires_at"],
            }

    def resolve_share_token(self, token: str) -> dict[str, Any] | None:
        """Wie ``resolve_share``, aber mit Klartext-Token (gleiche Signatur wie Supabase)."""
        from climate_dashboard.reports.sharing import hash_token, is_plausible_token

        if not is_plausible_token(token):
            return None
        return self.resolve_share(hash_token(token))

    # --- Einstellungen -----------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        rows = self._query("SELECT settings FROM user_settings WHERE id = 1")
        return json.loads(rows[0]["settings"]) if rows else {}

    def save_settings(self, settings: dict[str, Any]) -> None:
        payload = _json_dumps(dict(settings))
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO user_settings (id, settings, updated_at) VALUES (1, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET settings = excluded.settings, updated_at = excluded.updated_at",
                (payload, _dt_to_text(utcnow())),
            )


def _integrity_message(exc: sqlite3.IntegrityError) -> str:
    text = str(exc)
    if "shares.token_hash" in text:
        return "Diese Freigabe existiert bereits."
    if "report_versions" in text:
        return "Berichtsversionen sind unveränderlich bzw. die Versionsnummer existiert bereits."
    if "FOREIGN KEY" in text:
        return "Verknüpftes Objekt existiert nicht (Fremdschlüssel)."
    if "CHECK" in text:
        return f"Ungültiger Wert ({text})."
    return f"Eindeutigkeits- oder Integritätsverletzung ({text})."
