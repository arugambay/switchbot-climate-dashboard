"""Supabase-Implementierung des ``Repository``-Protokolls (Produktion).

* Zugriff ausschließlich mit dem anon-Key + JWT des angemeldeten Nutzers
  (siehe ``storage.factory.create_supabase_client``) → Row Level Security greift für jede
  Anfrage. Kein Service-Role-Key.
* ``owner_id`` wird nie vom Client gesetzt (Standardwert ``auth.uid()`` in der DB).
  Ausnahme: ``user_settings``-Upsert braucht den Konfliktschlüssel ``owner_id``; RLS prüft,
  dass er dem angemeldeten Nutzer entspricht.
* Messungen werden in Batches über die RPC ``import_measurements`` eingefügt
  (je Batch eine Transaktion, ``ON CONFLICT DO NOTHING``, exakte Anzahl eingefügter Zeilen).
  Mehrere Batches sind NICHT gemeinsam atomar – schlägt ein Batch fehl, entfernt der
  Import-Service per ``delete_measurements_of_import`` alle Zeilen dieses Imports.
* Zeitbereichsabfragen werden seitenweise geladen (PostgREST liefert standardmäßig
  höchstens 1000 Zeilen pro Anfrage): Keyset-Paginierung über ``ts`` pro Sensor mit
  ``.range(0, page_size - 1)`` – nutzt den Primärschlüssel-Index (sensor_id, ts).
* Öffentliche Berichte: ``resolve_share_token(token)`` ruft die RPC
  ``get_shared_report(p_token)`` auf (funktioniert ohne Login). ``resolve_share(token_hash)``
  aus dem Protokoll ist hier bewusst nicht nutzbar: Ein öffentlicher Endpunkt, der den Hash
  annimmt, würde den Schutz durch das Hashen aufheben (ein DB-Leak würde gültige Links
  liefern). Bitte ``reports.sharing.resolve_shared_report(repo, token)`` verwenden.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime
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
    utcnow,
)
from climate_dashboard.storage.base import IntegrityError, NotFoundError, RepositoryError

PAGE_SIZE = 1000  # ≤ PostgREST "max rows" (Supabase-Standard: 1000)
INSERT_BATCH_SIZE = 1000
CONFLICT_BATCH_SIZE = 500
SHARE_RESULT_KEYS = ("content", "version", "created_at", "expires_at")

_SENSOR_FIELDS = {"name", "device_info", "timezone", "expected_interval_s", "room_id"}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class SessionExpiredError(RepositoryError):
    """JWT abgelaufen/ungültig – die Oberfläche soll neu anmelden bzw. Sitzung erneuern."""


class PermissionDeniedError(RepositoryError):
    """Zugriff durch Row Level Security verweigert."""


class ConnectionFailedError(RepositoryError):
    """Supabase nicht erreichbar (Netzwerk, Zeitüberschreitung, Projekt pausiert)."""


# ---------------------------------------------------------------------------
# Fehlerübersetzung
# ---------------------------------------------------------------------------
def translate_error(exc: Exception) -> RepositoryError:
    """Übersetzt Fehler aus supabase-py/postgrest/httpx in verständliche deutsche Meldungen."""
    if isinstance(exc, RepositoryError):
        return exc

    code = str(getattr(exc, "code", "") or "")
    message = str(getattr(exc, "message", "") or exc)
    low = message.lower()
    status = _status_of(exc)

    if (
        code in ("PGRST301", "PGRST302", "PGRST303")
        or "jwt expired" in low
        or ("jwt" in low and "invalid" in low)
    ):
        return SessionExpiredError("Die Anmeldung ist abgelaufen. Bitte erneut anmelden.")
    if status == 401 or code == "401":
        return SessionExpiredError("Nicht angemeldet oder Sitzung abgelaufen. Bitte erneut anmelden.")
    if code == "42501" or status == 403 or code == "403" or "row-level security" in low:
        return PermissionDeniedError(
            "Keine Berechtigung für diese Aktion. Die Daten gehören einem anderen Nutzer "
            "oder die Anmeldung ist ungültig."
        )
    if code == "23505":
        if "rooms_active_name_uq" in low:
            return IntegrityError("Es gibt bereits einen aktiven Raum mit diesem Namen.")
        if "token_hash" in low:
            return IntegrityError("Diese Freigabe existiert bereits.")
        return IntegrityError("Eintrag existiert bereits (Eindeutigkeit verletzt).")
    if code == "23503":
        return IntegrityError("Verknüpftes Objekt existiert nicht oder gehört nicht zu Ihrem Konto.")
    if code in ("23514", "23502", "22023"):
        return IntegrityError(f"Ungültiger Wert: {message}")
    if code == "P0001" or "unveränderlich" in low:
        return IntegrityError(message)
    if code in ("PGRST116", "22P02"):
        return NotFoundError("Eintrag nicht gefunden.")
    if _is_network_error(exc):
        return ConnectionFailedError(
            "Keine Verbindung zur Datenbank (Supabase). Bitte Internetverbindung prüfen. "
            "Kostenlose Supabase-Projekte pausieren nach 7 Tagen ohne Nutzung und müssen im "
            "Supabase-Dashboard reaktiviert werden."
        )
    if status is not None and status >= 500:
        return ConnectionFailedError(
            "Die Datenbank ist vorübergehend nicht verfügbar. Bitte später erneut versuchen."
        )
    return RepositoryError(f"Datenbankfehler{f' ({code})' if code else ''}: {message}")


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_network_error(exc: Exception) -> bool:
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(exc, ConnectionError | TimeoutError)


# ---------------------------------------------------------------------------
# Zeit-/Wert-Konvertierung
# ---------------------------------------------------------------------------
def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise RepositoryError("Zeitangaben müssen eine Zeitzone haben (tz-aware, UTC).")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(UTC)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _clean_name(name: str, what: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise IntegrityError(f"{what} darf nicht leer sein.")
    return re.sub(r"\s+", " ", name.strip())


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


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
class SupabaseRepository:
    """``client``: ``supabase.Client`` mit anon-Key und Nutzer-JWT (bzw. Fake in Tests).
    ``user_id``: ID des angemeldeten Nutzers (``None`` für den öffentlichen Zugriff)."""

    def __init__(
        self,
        client: Any,
        user_id: str | None = None,
        *,
        page_size: int = PAGE_SIZE,
        insert_batch_size: int = INSERT_BATCH_SIZE,
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.page_size = page_size
        self.insert_batch_size = insert_batch_size

    # --- Infrastruktur -------------------------------------------------------
    def _run(self, builder: Any) -> Any:
        try:
            return builder.execute()
        except Exception as exc:
            raise translate_error(exc) from exc

    def _rows(self, builder: Any) -> list[dict[str, Any]]:
        data = self._run(builder).data
        if data is None:
            return []
        return data if isinstance(data, list) else [data]

    def _single(self, builder: Any, not_found: str) -> dict[str, Any]:
        rows = self._rows(builder)
        if not rows:
            raise NotFoundError(not_found)
        return rows[0]

    def _t(self, name: str) -> Any:
        return self.client.table(name)

    def _require_user(self) -> str:
        if not self.user_id:
            raise SessionExpiredError("Bitte zuerst anmelden.")
        return self.user_id

    # --- Räume -----------------------------------------------------------------
    @staticmethod
    def _room(r: dict[str, Any]) -> Room:
        return Room(
            id=r["id"],
            name=r["name"],
            description=r.get("description"),
            created_at=_dt(r.get("created_at")) or utcnow(),
            archived_at=_dt(r.get("archived_at")),
        )

    def list_rooms(self, include_archived: bool = False) -> list[Room]:
        q = self._t("rooms").select("*")
        if not include_archived:
            q = q.is_("archived_at", "null")
        return [self._room(r) for r in self._rows(q.order("name_norm").order("created_at"))]

    def get_room(self, room_id: str) -> Room:
        return self._room(
            self._single(self._t("rooms").select("*").eq("id", room_id), "Raum nicht gefunden.")
        )

    def create_room(self, name: str, description: str | None = None) -> Room:
        payload = {"name": _clean_name(name, "Raumname"), "description": description}
        return self._room(
            self._single(self._t("rooms").insert(payload), "Raum konnte nicht angelegt werden.")
        )

    def _update_room(self, room_id: str, payload: dict[str, Any]) -> Room:
        return self._room(
            self._single(self._t("rooms").update(payload).eq("id", room_id), "Raum nicht gefunden.")
        )

    def rename_room(self, room_id: str, new_name: str) -> Room:
        return self._update_room(room_id, {"name": _clean_name(new_name, "Raumname")})

    def update_room(self, room_id: str, *, description: str | None = None) -> Room:
        return self._update_room(room_id, {"description": description})

    def archive_room(self, room_id: str) -> Room:
        room = self.get_room(room_id)
        if room.archived_at is not None:
            return room
        return self._update_room(room_id, {"archived_at": _iso(utcnow())})

    def unarchive_room(self, room_id: str) -> Room:
        return self._update_room(room_id, {"archived_at": None})

    def delete_room(self, room_id: str) -> None:
        """Kaskadiert in der Datenbank (ON DELETE CASCADE): Sensoren, Messungen, Importe,
        Konflikte, Berichte, Versionen, Freigaben."""
        self._single(self._t("rooms").delete().eq("id", room_id), "Raum nicht gefunden.")

    # --- Sensoren --------------------------------------------------------------
    @staticmethod
    def _sensor(r: dict[str, Any]) -> Sensor:
        return Sensor(
            id=r["id"],
            room_id=r["room_id"],
            name=r["name"],
            device_info=r.get("device_info"),
            timezone=r.get("timezone") or "Europe/Berlin",
            expected_interval_s=r.get("expected_interval_s"),
            created_at=_dt(r.get("created_at")) or utcnow(),
            archived_at=_dt(r.get("archived_at")),
        )

    def list_sensors(self, room_id: str | None = None, include_archived: bool = False) -> list[Sensor]:
        q = self._t("sensors").select("*")
        if room_id is not None:
            q = q.eq("room_id", room_id)
        if not include_archived:
            q = q.is_("archived_at", "null")
        return [self._sensor(r) for r in self._rows(q.order("name").order("created_at"))]

    def get_sensor(self, sensor_id: str) -> Sensor:
        return self._sensor(
            self._single(self._t("sensors").select("*").eq("id", sensor_id), "Sensor nicht gefunden.")
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
        payload = {
            "room_id": room_id,
            "name": _clean_name(name, "Sensorname"),
            "device_info": device_info,
            "timezone": timezone,
            "expected_interval_s": expected_interval_s,
        }
        return self._sensor(
            self._single(self._t("sensors").insert(payload), "Sensor konnte nicht angelegt werden.")
        )

    def update_sensor(self, sensor_id: str, **fields: Any) -> Sensor:
        unknown = set(fields) - _SENSOR_FIELDS
        if unknown:
            raise RepositoryError(f"Nicht änderbare Sensorfelder: {', '.join(sorted(unknown))}")
        if "name" in fields:
            fields["name"] = _clean_name(fields["name"], "Sensorname")
        if not fields:
            return self.get_sensor(sensor_id)
        sensor = self._sensor(
            self._single(self._t("sensors").update(fields).eq("id", sensor_id), "Sensor nicht gefunden.")
        )
        if "room_id" in fields:
            # Importprotokolle wandern mit dem Sensor in den neuen Raum.
            self._run(self._t("imports").update({"room_id": fields["room_id"]}).eq("sensor_id", sensor_id))
        return sensor

    def archive_sensor(self, sensor_id: str) -> Sensor:
        sensor = self.get_sensor(sensor_id)
        if sensor.archived_at is not None:
            return sensor
        return self._sensor(
            self._single(
                self._t("sensors").update({"archived_at": _iso(utcnow())}).eq("id", sensor_id),
                "Sensor nicht gefunden.",
            )
        )

    def unarchive_sensor(self, sensor_id: str) -> Sensor:
        return self._sensor(
            self._single(
                self._t("sensors").update({"archived_at": None}).eq("id", sensor_id), "Sensor nicht gefunden."
            )
        )

    # --- Messungen -------------------------------------------------------------
    def load_measurements(
        self,
        sensor_ids: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        include_raw: bool = False,
    ) -> pd.DataFrame:
        """[start, end), sortiert nach ``ts``; seitenweise (Keyset) pro Sensor."""
        if not sensor_ids:
            return _empty_measurements(include_raw)
        cols = ["sensor_id", "ts", *METRICS] + (["raw_timestamp"] if include_raw else [])
        rows: list[dict[str, Any]] = []
        for sensor_id in dict.fromkeys(sensor_ids):
            last_ts: str | None = None
            while True:
                q = self._t("measurements").select(",".join(cols)).eq("sensor_id", sensor_id)
                if start is not None:
                    q = q.gte("ts", _iso(start))
                if end is not None:
                    q = q.lt("ts", _iso(end))
                if last_ts is not None:
                    q = q.gt("ts", last_ts)
                page = self._rows(q.order("ts").range(0, self.page_size - 1))
                rows.extend(page)
                if len(page) < self.page_size:
                    break
                last_ts = _iso(_dt(page[-1]["ts"]))
        if not rows:
            return _empty_measurements(include_raw)
        df = pd.DataFrame(rows, columns=cols)
        df[TS] = pd.to_datetime(df[TS], utc=True, format="ISO8601").astype("datetime64[ns, UTC]")
        for m in METRICS:
            df[m] = pd.to_numeric(df[m], errors="coerce").astype("float64")
        df[SENSOR_ID] = df[SENSOR_ID].astype("object")
        return df.sort_values([TS, SENSOR_ID], kind="stable").reset_index(drop=True)

    def measurement_extent(self, sensor_id: str) -> tuple[datetime | None, datetime | None, int]:
        data = self._run(self.client.rpc("measurement_extent", {"p_sensor": sensor_id})).data
        if isinstance(data, list):
            data = data[0] if data else None
        if not data or not data.get("count"):
            return None, None, 0
        return _dt(data.get("first_ts")), _dt(data.get("last_ts")), int(data["count"])

    def insert_measurements(self, sensor_id: str, import_id: str, df: pd.DataFrame) -> int:
        if df is None or len(df) == 0:
            return 0
        missing = [c for c in MEASUREMENT_COLUMNS if c not in df.columns]
        if missing:
            raise RepositoryError(f"Fehlende Spalten: {', '.join(missing)}")
        ts = pd.to_datetime(df[TS])
        if getattr(ts.dt, "tz", None) is None:
            raise RepositoryError("Spalte 'ts' muss tz-aware (UTC) sein.")
        ts_text = ts.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").tolist()
        values = {m: df[m].to_numpy(dtype="float64", na_value=np.nan) for m in METRICS}
        raw = df[RAW_TIMESTAMP].tolist() if RAW_TIMESTAMP in df.columns else [None] * len(df)
        payload = []
        for i in range(len(df)):
            row: dict[str, Any] = {"ts": ts_text[i]}
            for m in METRICS:
                row[m] = _float_or_none(values[m][i])
            r = raw[i]
            row["raw_timestamp"] = None if r is None or (not isinstance(r, str) and pd.isna(r)) else str(r)
            payload.append(row)
        inserted = 0
        for batch in _chunks(payload, self.insert_batch_size):
            result = self._run(
                self.client.rpc(
                    "import_measurements", {"p_import": import_id, "p_sensor": sensor_id, "p_rows": batch}
                )
            ).data
            inserted += int(result or 0)
        return inserted

    def delete_measurements_of_import(self, import_id: str) -> int:
        result = self._run(
            self._t("measurements").delete(count="exact", returning="minimal").eq("import_id", import_id)
        )
        return int(getattr(result, "count", None) or 0)

    def replace_measurement_values(
        self, sensor_id: str, ts: datetime, values: dict[str, float | None]
    ) -> None:
        unknown = set(values) - set(METRICS)
        if unknown:
            raise RepositoryError(f"Unbekannte Messgrößen: {', '.join(sorted(unknown))}")
        if not values:
            return
        payload = {k: _float_or_none(v) for k, v in values.items()}
        self._single(
            self._t("measurements").update(payload).eq("sensor_id", sensor_id).eq("ts", _iso(ts)),
            "Messung zu diesem Zeitpunkt nicht gefunden.",
        )

    # --- Importe ---------------------------------------------------------------
    @staticmethod
    def _import_payload(r: ImportRecord, *, include_keys: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "file_name": r.file_name,
            "file_sha256": r.file_sha256,
            "status": ImportStatus(r.status).value,
            "rows_read": r.rows_read,
            "rows_new": r.rows_new,
            "rows_duplicate": r.rows_duplicate,
            "rows_invalid": r.rows_invalid,
            "rows_conflict": r.rows_conflict,
            "ts_min": _iso(r.ts_min),
            "ts_max": _iso(r.ts_max),
            "expected_interval_s": r.expected_interval_s,
            "observed_interval_s": r.observed_interval_s,
            "warnings": list(r.warnings),
            "details": dict(r.details),
            "finished_at": _iso(r.finished_at),
        }
        if include_keys:
            payload.update(
                {"id": r.id, "sensor_id": r.sensor_id, "room_id": r.room_id, "created_at": _iso(r.created_at)}
            )
        return payload

    @staticmethod
    def _import(r: dict[str, Any]) -> ImportRecord:
        return ImportRecord(
            id=r["id"],
            sensor_id=r["sensor_id"],
            room_id=r["room_id"],
            file_name=r["file_name"],
            file_sha256=r["file_sha256"],
            status=ImportStatus(r["status"]),
            rows_read=r.get("rows_read") or 0,
            rows_new=r.get("rows_new") or 0,
            rows_duplicate=r.get("rows_duplicate") or 0,
            rows_invalid=r.get("rows_invalid") or 0,
            rows_conflict=r.get("rows_conflict") or 0,
            ts_min=_dt(r.get("ts_min")),
            ts_max=_dt(r.get("ts_max")),
            expected_interval_s=r.get("expected_interval_s"),
            observed_interval_s=r.get("observed_interval_s"),
            warnings=list(r.get("warnings") or []),
            details=dict(r.get("details") or {}),
            created_at=_dt(r.get("created_at")) or utcnow(),
            finished_at=_dt(r.get("finished_at")),
        )

    def create_import(self, record: ImportRecord) -> ImportRecord:
        return self._import(
            self._single(
                self._t("imports").insert(self._import_payload(record, include_keys=True)),
                "Import konnte nicht angelegt werden.",
            )
        )

    def update_import(self, record: ImportRecord) -> ImportRecord:
        return self._import(
            self._single(
                self._t("imports")
                .update(self._import_payload(record, include_keys=False))
                .eq("id", record.id),
                "Import nicht gefunden.",
            )
        )

    def get_import(self, import_id: str) -> ImportRecord:
        return self._import(
            self._single(self._t("imports").select("*").eq("id", import_id), "Import nicht gefunden.")
        )

    def list_imports(self, room_id: str | None = None, sensor_id: str | None = None) -> list[ImportRecord]:
        q = self._t("imports").select("*")
        if room_id is not None:
            q = q.eq("room_id", room_id)
        if sensor_id is not None:
            q = q.eq("sensor_id", sensor_id)
        return [self._import(r) for r in self._rows(q.order("created_at", desc=True))]

    def find_imports_by_hash(self, sensor_id: str, file_sha256: str) -> list[ImportRecord]:
        q = self._t("imports").select("*").eq("sensor_id", sensor_id).eq("file_sha256", file_sha256)
        return [self._import(r) for r in self._rows(q.order("created_at", desc=True))]

    # --- Konflikte -------------------------------------------------------------
    @staticmethod
    def _conflict(r: dict[str, Any]) -> MeasurementConflict:
        return MeasurementConflict(
            id=r["id"],
            sensor_id=r["sensor_id"],
            import_id=r["import_id"],
            ts=_dt(r["ts"]),
            existing=dict(r.get("existing") or {}),
            incoming=dict(r.get("incoming") or {}),
            resolution=ConflictResolution(r["resolution"]) if r.get("resolution") else None,
            resolved_at=_dt(r.get("resolved_at")),
            created_at=_dt(r.get("created_at")) or utcnow(),
        )

    def add_conflicts(self, conflicts: list[MeasurementConflict]) -> None:
        payload = [
            {
                "id": c.id,
                "sensor_id": c.sensor_id,
                "import_id": c.import_id,
                "ts": _iso(c.ts),
                "existing": {k: _float_or_none(v) for k, v in c.existing.items()},
                "incoming": {k: _float_or_none(v) for k, v in c.incoming.items()},
                "resolution": ConflictResolution(c.resolution).value if c.resolution else None,
                "resolved_at": _iso(c.resolved_at),
                "created_at": _iso(c.created_at),
            }
            for c in conflicts
        ]
        for batch in _chunks(payload, CONFLICT_BATCH_SIZE):
            self._run(self._t("measurement_conflicts").insert(batch, returning="minimal"))

    def delete_conflicts_of_import(self, import_id: str) -> int:
        result = self._run(
            self._t("measurement_conflicts").delete(count="exact", returning="minimal").eq("import_id", import_id)
        )
        return int(getattr(result, "count", None) or 0)

    def list_conflicts(
        self, sensor_id: str | None = None, unresolved_only: bool = True
    ) -> list[MeasurementConflict]:
        q = self._t("measurement_conflicts").select("*")
        if sensor_id is not None:
            q = q.eq("sensor_id", sensor_id)
        if unresolved_only:
            q = q.is_("resolution", "null")
        return [self._conflict(r) for r in self._rows(q.order("ts").order("created_at"))]

    def resolve_conflict(self, conflict_id: str, resolution: ConflictResolution) -> MeasurementConflict:
        """Hinweis: zwei Anfragen (Messwert ersetzen, Konflikt markieren) – nicht gemeinsam
        atomar. Reihenfolge so gewählt, dass ein Abbruch höchstens einen bereits ersetzten,
        aber noch offenen Konflikt hinterlässt (erneutes Auflösen ist dann harmlos)."""
        resolution = ConflictResolution(resolution)
        conflict = self._conflict(
            self._single(
                self._t("measurement_conflicts").select("*").eq("id", conflict_id), "Konflikt nicht gefunden."
            )
        )
        if conflict.resolution is not None:
            raise IntegrityError("Dieser Konflikt wurde bereits aufgelöst.")
        if resolution is ConflictResolution.USE_INCOMING:
            incoming = {k: v for k, v in conflict.incoming.items() if k in METRICS}
            self.replace_measurement_values(conflict.sensor_id, conflict.ts, incoming)
        row = self._single(
            self._t("measurement_conflicts")
            .update({"resolution": resolution.value, "resolved_at": _iso(utcnow())})
            .eq("id", conflict_id)
            .is_("resolution", "null"),
            "Konflikt nicht gefunden.",
        )
        return self._conflict(row)

    # --- Berichte --------------------------------------------------------------
    @staticmethod
    def _report(r: dict[str, Any]) -> Report:
        return Report(
            id=r["id"],
            room_id=r["room_id"],
            title=r["title"],
            current_version_id=r.get("current_version_id"),
            created_at=_dt(r.get("created_at")) or utcnow(),
            updated_at=_dt(r.get("updated_at")) or utcnow(),
            archived_at=_dt(r.get("archived_at")),
        )

    @staticmethod
    def _version(r: dict[str, Any]) -> ReportVersion:
        return ReportVersion(
            id=r["id"],
            report_id=r["report_id"],
            version=int(r["version"]),
            period_start=_dt(r["period_start"]),
            period_end=_dt(r["period_end"]),
            config=dict(r.get("config") or {}),
            content=dict(r.get("content") or {}),
            data_fingerprint=r["data_fingerprint"],
            created_at=_dt(r.get("created_at")) or utcnow(),
        )

    def create_report(self, room_id: str, title: str) -> Report:
        payload = {"room_id": room_id, "title": _clean_name(title, "Titel")}
        return self._report(
            self._single(self._t("reports").insert(payload), "Bericht konnte nicht angelegt werden.")
        )

    def get_report(self, report_id: str) -> Report:
        return self._report(
            self._single(self._t("reports").select("*").eq("id", report_id), "Bericht nicht gefunden.")
        )

    def list_reports(self, room_id: str | None = None, include_archived: bool = False) -> list[Report]:
        q = self._t("reports").select("*")
        if room_id is not None:
            q = q.eq("room_id", room_id)
        if not include_archived:
            q = q.is_("archived_at", "null")
        return [self._report(r) for r in self._rows(q.order("updated_at", desc=True))]

    def _update_report(self, report_id: str, payload: dict[str, Any]) -> Report:
        payload = {**payload, "updated_at": _iso(utcnow())}
        return self._report(
            self._single(self._t("reports").update(payload).eq("id", report_id), "Bericht nicht gefunden.")
        )

    def update_report(self, report_id: str, *, title: str | None = None) -> Report:
        if title is None:
            return self.get_report(report_id)
        return self._update_report(report_id, {"title": _clean_name(title, "Titel")})

    def archive_report(self, report_id: str) -> Report:
        report = self.get_report(report_id)
        if report.archived_at is not None:
            return report
        return self._update_report(report_id, {"archived_at": _iso(utcnow())})

    def delete_report(self, report_id: str) -> None:
        self._single(self._t("reports").delete().eq("id", report_id), "Bericht nicht gefunden.")

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
        """Die Versionsnummer vergibt ein Datenbank-Trigger (fortlaufend, gesperrt pro Bericht)."""
        payload = {
            "report_id": report_id,
            "period_start": _iso(period_start),
            "period_end": _iso(period_end),
            "config": config,
            "content": content,
            "data_fingerprint": data_fingerprint,
        }
        version = self._version(
            self._single(self._t("report_versions").insert(payload), "Version konnte nicht angelegt werden.")
        )
        if make_current:
            self._update_report(report_id, {"current_version_id": version.id})
        return version

    def list_report_versions(self, report_id: str) -> list[ReportVersion]:
        q = self._t("report_versions").select("*").eq("report_id", report_id).order("version")
        return [self._version(r) for r in self._rows(q)]

    def get_report_version(self, version_id: str) -> ReportVersion:
        return self._version(
            self._single(
                self._t("report_versions").select("*").eq("id", version_id), "Berichtsversion nicht gefunden."
            )
        )

    def set_current_version(self, report_id: str, version_id: str) -> Report:
        self._single(
            self._t("report_versions").select("id").eq("id", version_id).eq("report_id", report_id),
            "Version gehört nicht zu diesem Bericht.",
        )
        return self._update_report(report_id, {"current_version_id": version_id})

    # --- Freigaben -------------------------------------------------------------
    @staticmethod
    def _share(r: dict[str, Any]) -> Share:
        return Share(
            id=r["id"],
            report_id=r["report_id"],
            token_hash=r["token_hash"],
            token_hint=r["token_hint"],
            label=r.get("label"),
            created_at=_dt(r.get("created_at")) or utcnow(),
            expires_at=_dt(r.get("expires_at")),
            revoked_at=_dt(r.get("revoked_at")),
            last_accessed_at=_dt(r.get("last_accessed_at")),
            access_count=int(r.get("access_count") or 0),
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
        payload = {
            "report_id": report_id,
            "token_hash": token_hash,
            "token_hint": token_hint,
            "label": label,
            "expires_at": _iso(expires_at),
        }
        return self._share(
            self._single(self._t("shares").insert(payload), "Freigabe konnte nicht angelegt werden.")
        )

    def list_shares(self, report_id: str | None = None) -> list[Share]:
        q = self._t("shares").select("*")
        if report_id is not None:
            q = q.eq("report_id", report_id)
        return [self._share(r) for r in self._rows(q.order("created_at", desc=True))]

    def revoke_share(self, share_id: str) -> Share:
        rows = self._rows(
            self._t("shares")
            .update({"revoked_at": _iso(utcnow())})
            .eq("id", share_id)
            .is_("revoked_at", "null")
        )
        if rows:
            return self._share(rows[0])
        # bereits widerrufen (oder nicht vorhanden)
        return self._share(
            self._single(self._t("shares").select("*").eq("id", share_id), "Freigabe nicht gefunden.")
        )

    def resolve_share(self, token_hash: str) -> dict[str, Any] | None:
        raise RepositoryError(
            "Im Supabase-Modus wird ein geteilter Bericht über den Klartext-Token geladen: "
            "reports.sharing.resolve_shared_report(repo, token) bzw. repo.resolve_share_token(token) verwenden."
        )

    def resolve_share_token(self, token: str) -> dict[str, Any] | None:
        """Öffentlicher Abruf über die RPC ``get_shared_report`` (ohne Login möglich)."""
        from climate_dashboard.reports.sharing import is_plausible_token

        if not is_plausible_token(token):
            return None
        data = self._run(self.client.rpc("get_shared_report", {"p_token": token})).data
        if isinstance(data, list):
            data = data[0] if data else None
        if not data or not isinstance(data, dict):
            return None
        # Nur die erlaubten Felder weiterreichen (Verteidigung in der Tiefe).
        return {k: data.get(k) for k in SHARE_RESULT_KEYS}

    # --- Einstellungen -----------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        rows = self._rows(self._t("user_settings").select("settings").limit(1))
        return dict(rows[0].get("settings") or {}) if rows else {}

    def save_settings(self, settings: dict[str, Any]) -> None:
        payload = {"owner_id": self._require_user(), "settings": dict(settings), "updated_at": _iso(utcnow())}
        self._run(self._t("user_settings").upsert(payload, on_conflict="owner_id", returning="minimal"))
