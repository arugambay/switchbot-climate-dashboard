"""Logik des Import-Assistenten – ohne Streamlit, damit sie direkt testbar ist.

Die Seite ``ui/pages/import_wizard.py`` hält nur Widgets und Sitzungszustand; alles, was
entscheidet (Zuordnung prüfen, Vorschau-Cache, Räume/Sensoren anlegen, Import ausführen),
steht hier.

Grundsätze:

* Jede Datei hat genau einen Ziel-Sensor in genau einem Raum.
* Raumnamen aus dem Dateinamen sind nur Vorschläge; ähnliche vorhandene Räume werden nur
  angezeigt, nie automatisch zusammengeführt.
* Neue Räume/Sensoren werden erst beim Bestätigen des Imports angelegt – Abbrechen
  hinterlässt nichts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from climate_dashboard.domain.models import AnalysisSettings, ImportRecord, Room, Sensor
from climate_dashboard.domain.rooms import (
    find_similar_rooms,
    is_duplicate_name,
    normalize_room_name,
    suggest_room_name_from_filename,
    suggest_sensor_name_from_filename,
    validate_room_name,
)
from climate_dashboard.ingest.intervals import INTERVAL_PRESETS
from climate_dashboard.ingest.service import (
    DataImportError,
    FileAssignment,
    ImportPreview,
    check_file_assignments,
    commit_import,
    preview_import,
)
from climate_dashboard.ingest.validation import Issue, Severity

NEW = "new"
EXISTING = "existing"
CUSTOM_INTERVAL = "Benutzerdefiniert"
AUTO_INTERVAL = "Automatisch erkennen"
INTERVAL_UNITS: dict[str, int] = {"Sekunden": 1, "Minuten": 60, "Stunden": 3600}
STEPS: tuple[str, ...] = (
    "Dateien",
    "Raum",
    "Sensor",
    "Intervall",
    "Vorschau",
    "Prüfung",
    "Import",
    "Ergebnis",
)
MAX_SENSOR_NAME_LENGTH = 80
DEFAULT_SENSOR_NAME = "Sensor"


# ---------------------------------------------------------------------------
# Zuordnung je Datei
# ---------------------------------------------------------------------------
@dataclass
class FileEntry:
    """Eine hochgeladene Datei und ihre (noch nicht gespeicherte) Zuordnung."""

    file_id: str  # SHA-256 des Inhalts
    name: str
    content: bytes
    room_mode: str = NEW  # NEW | EXISTING
    room_id: str | None = None
    new_room_name: str = ""
    sensor_mode: str = NEW
    sensor_id: str | None = None
    new_sensor_name: str = ""
    timezone: str = "Europe/Berlin"
    interval_label: str = AUTO_INTERVAL
    custom_interval_value: float = 15.0
    custom_interval_unit: str = "Minuten"

    @property
    def size(self) -> int:
        return len(self.content)


def file_id_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def new_entry(name: str, content: bytes, default_timezone: str = "Europe/Berlin") -> FileEntry:
    """Legt einen Eintrag mit Vorschlägen an (Raumname/Sensorname aus dem Dateinamen).

    Es wird immer zunächst ein *neuer* Raum mit dem vorgeschlagenen Namen vorgeschlagen –
    auch wenn ein ähnlicher Raum existiert; der Hinweis darauf erscheint in der Oberfläche.
    Ohne Vorschlag bleibt das Feld leer (keine erzwungenen Standardnamen).
    """
    return FileEntry(
        file_id=file_id_of(content),
        name=name,
        content=content,
        room_mode=NEW,
        new_room_name=suggest_room_name_from_filename(name) or "",
        sensor_mode=NEW,
        new_sensor_name=suggest_sensor_name_from_filename(name) or DEFAULT_SENSOR_NAME,
        timezone=default_timezone,
    )


def add_files(
    entries: list[FileEntry],
    files: Iterable[tuple[str, bytes]],
    default_timezone: str = "Europe/Berlin",
) -> tuple[list[FileEntry], list[str]]:
    """Ergänzt neue Dateien (gleicher Inhalt wird nur einmal übernommen).

    Rückgabe: (aktualisierte Liste, Namen übersprungener Doppel-Uploads).
    """
    known = {e.file_id for e in entries}
    result = list(entries)
    skipped: list[str] = []
    for name, content in files:
        fid = file_id_of(content)
        if fid in known:
            skipped.append(name)
            continue
        known.add(fid)
        result.append(new_entry(name, content, default_timezone))
    return result, skipped


def similar_room_hints(
    entry: FileEntry, rooms: Sequence[Room], threshold: float = 0.8
) -> list[tuple[Room, float]]:
    """Ähnliche vorhandene Räume zum eingegebenen neuen Raumnamen (nur Hinweis)."""
    if entry.room_mode != NEW or not entry.new_room_name.strip():
        return []
    return find_similar_rooms(entry.new_room_name, rooms, threshold)


def use_existing_room(entry: FileEntry, room: Room) -> None:
    """Explizite Nutzerentscheidung „Vorhandenen Raum verwenden“."""
    entry.room_mode = EXISTING
    entry.room_id = room.id
    if entry.sensor_mode == EXISTING:
        entry.sensor_id = None


def interval_seconds(entry: FileEntry) -> int | None:
    """Erwartetes Messintervall in Sekunden (``None`` = automatisch erkennen)."""
    if entry.interval_label == CUSTOM_INTERVAL:
        factor = INTERVAL_UNITS.get(entry.custom_interval_unit, 60)
        value = int(round(float(entry.custom_interval_value) * factor))
        return value if value > 0 else None
    return INTERVAL_PRESETS.get(entry.interval_label)


def interval_label_for(seconds: int | None) -> tuple[str, float, str]:
    """Umkehrung: (Preset-Bezeichnung, benutzerdefinierter Wert, Einheit) für einen Sekundenwert."""
    for label, value in INTERVAL_PRESETS.items():
        if value == seconds:
            return label, 15.0, "Minuten"
    if seconds and seconds % 3600 == 0:
        return CUSTOM_INTERVAL, seconds / 3600, "Stunden"
    if seconds and seconds % 60 == 0:
        return CUSTOM_INTERVAL, seconds / 60, "Minuten"
    return CUSTOM_INTERVAL, float(seconds or 15), "Sekunden"


def room_label(entry: FileEntry, rooms_by_id: dict[str, Room]) -> str:
    if entry.room_mode == EXISTING and entry.room_id in rooms_by_id:
        return rooms_by_id[entry.room_id].name
    name = entry.new_room_name.strip()
    return f"{name} (neu)" if name else "–"


def sensor_label(entry: FileEntry, sensors_by_id: dict[str, Sensor]) -> str:
    if entry.sensor_mode == EXISTING and entry.sensor_id in sensors_by_id:
        return sensors_by_id[entry.sensor_id].name
    name = entry.new_sensor_name.strip()
    return f"{name} (neu)" if name else "–"


def assignment_rows(
    entries: Sequence[FileEntry], rooms: Sequence[Room], sensors: Sequence[Sensor]
) -> list[dict[str, str]]:
    """Tabelle Datei → Raum → Sensor für die Anzeige der aktuellen Zuordnung."""
    rooms_by_id = {r.id: r for r in rooms}
    sensors_by_id = {s.id: s for s in sensors}
    return [
        {"Datei": e.name, "Raum": room_label(e, rooms_by_id), "Sensor": sensor_label(e, sensors_by_id)}
        for e in entries
    ]


def room_errors(entry: FileEntry, all_rooms: Sequence[Room]) -> list[str]:
    """Fehler der Raumzuordnung (``all_rooms`` inkl. archivierter Räume)."""
    if entry.room_mode == EXISTING:
        if not entry.room_id or entry.room_id not in {r.id for r in all_rooms}:
            return ["Bitte einen vorhandenen Raum auswählen."]
        return []
    error = validate_room_name(entry.new_room_name)
    if error:
        return [error]
    if is_duplicate_name(entry.new_room_name, all_rooms):
        return [
            f"Ein Raum mit dem Namen „{entry.new_room_name.strip()}“ existiert bereits. Bitte den vorhandenen "
            "Raum verwenden oder einen anderen Namen wählen."
        ]
    return []


def sensor_errors(entry: FileEntry, sensors: Sequence[Sensor]) -> list[str]:
    """Fehler der Sensorzuordnung. Ein vorhandener Sensor muss zum gewählten Raum gehören."""
    if entry.sensor_mode == EXISTING:
        sensor = next((s for s in sensors if s.id == entry.sensor_id), None)
        if sensor is None:
            return ["Bitte einen vorhandenen Sensor auswählen."]
        if entry.room_mode != EXISTING or sensor.room_id != entry.room_id:
            return ["Der gewählte Sensor gehört nicht zum gewählten Raum."]
        return []
    name = entry.new_sensor_name.strip()
    if not name:
        return ["Bitte einen Namen für den neuen Sensor eingeben."]
    if len(name) > MAX_SENSOR_NAME_LENGTH:
        return [f"Der Sensorname darf höchstens {MAX_SENSOR_NAME_LENGTH} Zeichen lang sein."]
    if not entry.timezone:
        return ["Bitte eine Zeitzone wählen."]
    return []


def target_key(entry: FileEntry) -> str:
    """Stabiler Schlüssel des Ziel-Sensors – auch für noch nicht angelegte Sensoren.

    Mehrere Dateien mit demselben neuen Raum- und Sensornamen landen im selben neuen Sensor.
    """
    if entry.sensor_mode == EXISTING and entry.sensor_id:
        return entry.sensor_id
    room = entry.room_id if entry.room_mode == EXISTING else f"neu:{normalize_room_name(entry.new_room_name)}"
    return f"{room}/neu:{normalize_room_name(entry.new_sensor_name)}"


def existing_sensor_id(entry: FileEntry) -> str | None:
    return entry.sensor_id if entry.sensor_mode == EXISTING else None


def sensor_timezone(entry: FileEntry, sensors: Sequence[Sensor]) -> str:
    if entry.sensor_mode == EXISTING:
        sensor = next((s for s in sensors if s.id == entry.sensor_id), None)
        if sensor is not None:
            return sensor.timezone
    return entry.timezone or "Europe/Berlin"


# ---------------------------------------------------------------------------
# Vorschau (mit Cache)
# ---------------------------------------------------------------------------
def preview_key(entry: FileEntry, sensors: Sequence[Sensor], settings: AnalysisSettings) -> tuple:
    """Cache-Schlüssel: Inhalt + Ziel-Sensor + Zeitzone + Intervall + Lückenparameter."""
    return (
        entry.file_id,
        existing_sensor_id(entry),
        sensor_timezone(entry, sensors),
        interval_seconds(entry),
        settings.gap_factor,
        settings.min_gap_minutes,
    )


def get_preview(
    cache: dict[tuple, ImportPreview],
    repo: Any,
    entry: FileEntry,
    sensors: Sequence[Sensor],
    settings: AnalysisSettings,
) -> ImportPreview:
    """Liefert die Vorschau aus dem Cache oder berechnet sie (Datei wird nur einmal je
    Schlüssel gelesen)."""
    key = preview_key(entry, sensors, settings)
    if key not in cache:
        cache[key] = preview_import(
            repo,
            entry.content,
            entry.name,
            sensor_id=existing_sensor_id(entry),
            timezone=sensor_timezone(entry, sensors),
            expected_interval_s=interval_seconds(entry),
            settings=settings,
        )
    return cache[key]


def session_issues(entries: Sequence[FileEntry], previews: dict[str, ImportPreview]) -> list[Issue]:
    """Prüfung über alle Dateien einer Sitzung (gleiche Dateien, Überlappungen je Sensor)."""
    if len(entries) < 2:
        return []
    assignments = [
        FileAssignment.from_preview(previews[e.file_id], target_key(e))
        for e in entries
        if e.file_id in previews
    ]
    return check_file_assignments(assignments)


def group_issues(issues: Iterable[Issue]) -> dict[Severity, list[Issue]]:
    """Meldungen nach Schweregrad (Reihenfolge: Fehler, Warnungen, Hinweise)."""
    groups: dict[Severity, list[Issue]] = {Severity.ERROR: [], Severity.WARNING: [], Severity.INFO: []}
    for issue in issues:
        groups[Severity(issue.severity)].append(issue)
    return groups


def blocking_reasons(
    entries: Sequence[FileEntry],
    all_rooms: Sequence[Room],
    sensors: Sequence[Sensor],
    previews: dict[str, ImportPreview],
) -> list[str]:
    """Gründe, warum der Import nicht gestartet werden kann (leer = bereit)."""
    reasons: list[str] = []
    if not entries:
        return ["Es wurde noch keine Datei ausgewählt."]
    for e in entries:
        for msg in room_errors(e, all_rooms) + sensor_errors(e, sensors):
            reasons.append(f"{e.name}: {msg}")
        p = previews.get(e.file_id)
        if p is None:
            reasons.append(f"{e.name}: Die Vorschau wurde noch nicht erstellt.")
        elif not p.can_import:
            reasons.append(f"{e.name}: Die Datei enthält Fehler und kann nicht importiert werden.")
    if any(i.severity == Severity.ERROR for i in session_issues(entries, previews)):
        reasons.append("Die Zuordnung der Dateien ist unvollständig.")
    return reasons


# ---------------------------------------------------------------------------
# Durchführung
# ---------------------------------------------------------------------------
@dataclass
class ImportOutcome:
    file_name: str
    record: ImportRecord | None = None
    error: str | None = None
    room_id: str | None = None
    sensor_id: str | None = None


@dataclass
class CreatedTargets:
    rooms: list[Room] = field(default_factory=list)
    sensors: list[Sensor] = field(default_factory=list)


def resolve_targets(
    repo: Any, entries: Sequence[FileEntry]
) -> tuple[dict[str, tuple[str, str]], CreatedTargets]:
    """Legt erst jetzt benötigte neue Räume/Sensoren an.

    Gleiche neue Raumnamen (normalisiert) bzw. gleiche neue Sensoren im selben Raum werden nur
    einmal angelegt. Rückgabe: ({file_id: (room_id, sensor_id)}, angelegte Objekte).
    """
    created = CreatedTargets()
    new_rooms: dict[str, Room] = {}
    new_sensors: dict[tuple[str, str], Sensor] = {}
    result: dict[str, tuple[str, str]] = {}
    for e in entries:
        if e.room_mode == EXISTING and e.room_id:
            room_id = e.room_id
        else:
            key = normalize_room_name(e.new_room_name)
            if key not in new_rooms:
                room = repo.create_room(e.new_room_name.strip())
                new_rooms[key] = room
                created.rooms.append(room)
            room_id = new_rooms[key].id
        if e.sensor_mode == EXISTING and e.sensor_id:
            sensor_id = e.sensor_id
        else:
            skey = (room_id, normalize_room_name(e.new_sensor_name))
            if skey not in new_sensors:
                sensor = repo.create_sensor(
                    room_id,
                    e.new_sensor_name.strip(),
                    timezone=e.timezone,
                    expected_interval_s=interval_seconds(e),
                )
                new_sensors[skey] = sensor
                created.sensors.append(sensor)
            sensor_id = new_sensors[skey].id
        result[e.file_id] = (room_id, sensor_id)
    return result, created


def run_import(
    repo: Any,
    entries: Sequence[FileEntry],
    previews: dict[str, ImportPreview],
    *,
    progress: Callable[[float, str], None] | None = None,
) -> list[ImportOutcome]:
    """Legt Ziele an und importiert alle Dateien nacheinander.

    Ein Fehler in einer Datei bricht die übrigen nicht ab; der fehlgeschlagene Import wurde
    von ``commit_import`` bereits zurückgerollt.
    """
    targets, _ = resolve_targets(repo, entries)
    outcomes: list[ImportOutcome] = []
    total = len(entries)
    for i, e in enumerate(entries):
        room_id, sensor_id = targets[e.file_id]

        def file_progress(fraction: float, text: str, i: int = i, name: str = e.name) -> None:
            if progress is not None:
                progress((i + fraction) / total, f"{name}: {text}")

        try:
            record = commit_import(
                repo, previews[e.file_id], sensor_id=sensor_id, room_id=room_id, progress=file_progress
            )
            outcomes.append(ImportOutcome(e.name, record=record, room_id=room_id, sensor_id=sensor_id))
        except DataImportError as exc:
            outcomes.append(ImportOutcome(e.name, error=str(exc), room_id=room_id, sensor_id=sensor_id))
    if progress is not None:
        progress(1.0, "Fertig")
    return outcomes


def outcome_summary(outcome: ImportOutcome) -> dict[str, Any]:
    """Kennzahlen des Importprotokolls für die Ergebnisanzeige."""
    r = outcome.record
    if r is None:
        return {"Datei": outcome.file_name, "Status": "fehlgeschlagen", "Fehler": outcome.error}
    return {
        "Datei": outcome.file_name,
        "Status": {
            "completed": "abgeschlossen",
            "rolled_back": "zurückgerollt",
            "failed": "fehlgeschlagen",
        }.get(str(r.status), str(r.status)),
        "Gelesen": r.rows_read,
        "Neu": r.rows_new,
        "Bereits vorhanden": r.rows_duplicate,
        "Ungültig": r.rows_invalid,
        "Konflikte": r.rows_conflict,
        "Von": r.ts_min,
        "Bis": r.ts_max,
        "Warnungen": len(r.warnings),
    }
