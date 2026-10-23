"""Import-Service: Vorschau und Durchführung eines CSV-Imports.

Ablauf:

1. :func:`preview_import` liest die Datei, analysiert Intervalle/Lücken, prüft Plausibilität
   und – falls der Ziel-Sensor bekannt ist – vergleicht mit vorhandenen Messungen (nur der
   betroffene Zeitraum wird geladen). Es wird nichts gespeichert.
2. :func:`commit_import` legt ein Importprotokoll an, klassifiziert unmittelbar vor dem
   Schreiben erneut (falls inzwischen Daten hinzugekommen sind), schreibt neue Messungen in
   Batches, speichert Konflikte und schließt das Protokoll ab. Bei einem Fehler werden alle
   Messungen dieses Imports wieder entfernt (Rollback).

Jede Datei hat genau einen Ziel-Sensor; Daten verschiedener Sensoren werden nie gemischt.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from climate_dashboard.domain.models import (
    METRICS,
    RAW_TIMESTAMP,
    TS,
    AnalysisSettings,
    ImportRecord,
    ImportStatus,
    MeasurementConflict,
    new_id,
    utcnow,
)
from climate_dashboard.ingest.csv_reader import ParsedCsv, parse_csv
from climate_dashboard.ingest.dedup import DedupResult, classify
from climate_dashboard.ingest.intervals import IntervalReport, analyze_intervals, format_duration_de
from climate_dashboard.ingest.validation import Issue, Severity, has_errors

ProgressCallback = Callable[[float, str], None]


class DataImportError(Exception):
    """Basisklasse für Importfehler mit verständlicher deutscher Meldung (``str(exc)``).

    Bewusst nicht ``ImportError`` genannt, um das gleichnamige Python-Builtin nicht zu
    verdecken.
    """


class ImportBlockedError(DataImportError):
    """Der Import ist wegen ERROR-Meldungen nicht möglich."""


class ImportCommitError(DataImportError):
    """Beim Speichern ist ein Fehler aufgetreten; der Import wurde zurückgerollt."""

    def __init__(self, message: str, record: ImportRecord | None = None) -> None:
        super().__init__(message)
        self.record = record


def _ts_to_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").to_pydatetime()


@dataclass
class ImportPreview:
    """Vorschau eines Imports (nichts gespeichert).

    ``issues`` enthält alle Meldungen (Datei, Plausibilität, Intervalle, Duplikate, bereits
    importierte Datei). ``dedup.new`` sind die Zeilen, die importiert würden.
    ``checked_against_existing`` ist False, wenn noch kein Sensor gewählt war.
    """

    parsed: ParsedCsv
    intervals: IntervalReport
    dedup: DedupResult
    sensor_id: str | None
    timezone: str
    expected_interval_s: int | None
    issues: list[Issue] = field(default_factory=list)
    already_imported: list[ImportRecord] = field(default_factory=list)
    checked_against_existing: bool = False

    @property
    def file_name(self) -> str:
        return self.parsed.file_name

    @property
    def sha256(self) -> str:
        return self.parsed.sha256

    @property
    def can_import(self) -> bool:
        return not has_errors(self.issues) and len(self.parsed.data) > 0

    @property
    def counts(self) -> dict[str, int]:
        c = self.dedup.counts
        return {
            "rows_read": int(self.parsed.rows_read),
            "rows_valid": int(len(self.parsed.data)),
            "rows_invalid": int(len(self.parsed.invalid_rows)),
            "rows_new": c["new"],
            "rows_duplicate": c["duplicate"] + c["in_file_duplicates"],
            "rows_conflict": c["conflict"] + c["in_file_conflict_rows"],
            "empty_lines": int(self.parsed.empty_lines),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "sha256": self.sha256,
            "sensor_id": self.sensor_id,
            "timezone": self.timezone,
            "expected_interval_s": self.expected_interval_s,
            "can_import": self.can_import,
            "checked_against_existing": self.checked_against_existing,
            "counts": self.counts,
            "issues": [i.to_dict() for i in self.issues],
            "detection": self.parsed.detection,
            "intervals": self.intervals.to_dict(),
            "dedup": self.dedup.to_dict(),
            "already_imported": [
                {"id": r.id, "created_at": r.created_at.isoformat(), "file_name": r.file_name}
                for r in self.already_imported
            ],
        }


# ---------------------------------------------------------------------------
# Meldungen aus Intervall- und Duplikatanalyse
# ---------------------------------------------------------------------------
def interval_issues(report: IntervalReport) -> list[Issue]:
    issues: list[Issue] = []
    if report.deviates_from_expected and report.deviation_text:
        issues.append(
            Issue(
                Severity.WARNING,
                "interval_deviates",
                report.deviation_text + " Lücken und Vollständigkeit werden mit dem "
                "angegebenen Intervall berechnet.",
            )
        )
    if report.gaps:
        longest = report.longest_gap
        assert longest is not None
        issues.append(
            Issue(
                Severity.INFO,
                "gaps",
                f"Die Daten enthalten {len(report.gaps)} Lücke(n) von mehr als "
                f"{format_duration_de(report.gap_threshold_s)}. Die längste dauert "
                f"{format_duration_de(longest.duration_s)}. Lücken werden nicht aufgefüllt.",
                len(report.gaps),
                [
                    f"{g.start.isoformat()} bis {g.end.isoformat()}"
                    for g in sorted(report.gaps, key=lambda g: -g.duration_s)[:5]
                ],
            )
        )
    if report.share_irregular_pct is not None and report.share_irregular_pct > 20:
        issues.append(
            Issue(
                Severity.INFO,
                "interval_irregular",
                f"Die Abstände zwischen den Messungen sind unregelmäßig ({report.share_irregular_pct:.0f} % "
                "weichen vom typischen Intervall ab). Auswertungen werden deshalb zeitgewichtet berechnet.",
            )
        )
    if report.interval_changes:
        issues.append(
            Issue(
                Severity.INFO,
                "interval_changes",
                "Das Messintervall ändert sich im Verlauf der Datei: "
                + ", ".join(format_duration_de(s.interval_s) for s in report.interval_changes)
                + ".",
                len(report.interval_changes),
            )
        )
    return issues


def dedup_issues(result: DedupResult, checked_against_existing: bool) -> list[Issue]:
    issues: list[Issue] = []
    if result.in_file_duplicates:
        issues.append(
            Issue(
                Severity.INFO,
                "in_file_duplicates",
                "Die Datei enthält identische doppelte Zeilen; sie werden nur einmal übernommen.",
                result.in_file_duplicates,
            )
        )
    if result.in_file_conflicts:
        issues.append(
            Issue(
                Severity.WARNING,
                "in_file_conflicts",
                "Für einige Zeitpunkte enthält die Datei mehrere Zeilen mit unterschiedlichen Werten. "
                "Da unklar ist, welche stimmt, wird keine davon importiert.",
                result.in_file_conflict_rows,
                [
                    f"{c.ts.isoformat()} (Zeilen {', '.join(map(str, c.lines))})"
                    for c in result.in_file_conflicts[:5]
                ],
            )
        )
    if checked_against_existing:
        if len(result.duplicates):
            issues.append(
                Issue(
                    Severity.INFO,
                    "already_present",
                    "Ein Teil der Messungen ist bereits gespeichert und wird übersprungen.",
                    len(result.duplicates),
                )
            )
        if result.conflicts:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "conflicts",
                    "Für einige Zeitpunkte sind bereits andere Werte gespeichert. Es wird nichts "
                    "überschrieben; die Abweichungen werden zur Prüfung gespeichert.",
                    len(result.conflicts),
                    [c.ts.isoformat() for c in result.conflicts[:5]],
                )
            )
        if len(result.new) == 0:
            issues.append(Issue(Severity.INFO, "nothing_new", "Die Datei enthält keine neuen Messungen."))
    return issues


def _load_existing(repo: Any, sensor_id: str, data: pd.DataFrame) -> pd.DataFrame:
    """Lädt nur den Zeitraum der Datei (halboffen → Ende + 1 s)."""
    start = data[TS].min().floor("s").to_pydatetime()
    end = (data[TS].max().floor("s") + timedelta(seconds=1)).to_pydatetime()
    return repo.load_measurements([sensor_id], start=start, end=end)


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------
def preview_import(
    repo: Any,
    content: bytes,
    file_name: str,
    *,
    sensor_id: str | None,
    timezone: str = "Europe/Berlin",
    expected_interval_s: int | None = None,
    settings: AnalysisSettings | None = None,
) -> ImportPreview:
    """Erstellt die Importvorschau. Speichert nichts.

    ``repo`` muss das ``Repository``-Protokoll erfüllen (genutzt: ``load_measurements``,
    ``find_imports_by_hash``). Ohne ``sensor_id`` werden nur Duplikate/Konflikte innerhalb
    der Datei geprüft.
    """
    parsed = parse_csv(content, file_name, timezone=timezone)
    intervals = analyze_intervals(parsed.data[TS], expected_interval_s, settings=settings)
    issues = list(parsed.issues)
    issues.extend(interval_issues(intervals))
    compare = parsed.metrics_present or None
    existing = None
    already: list[ImportRecord] = []
    checked = False
    if sensor_id and len(parsed.data):
        existing = _load_existing(repo, sensor_id, parsed.data)
        checked = True
        already = [
            r
            for r in repo.find_imports_by_hash(sensor_id, parsed.sha256)
            if r.status == ImportStatus.COMPLETED
        ]
    dedup = classify(parsed.data, existing, compare_metrics=compare)
    issues.extend(dedup_issues(dedup, checked))
    if already:
        when = ", ".join(r.created_at.strftime("%d.%m.%Y %H:%M") for r in already)
        issues.append(
            Issue(
                Severity.WARNING,
                "file_already_imported",
                f"Genau diese Datei wurde für diesen Sensor bereits importiert ({when}). Ein erneuter "
                "Import fügt keine doppelten Messungen hinzu.",
                len(already),
            )
        )
    return ImportPreview(
        parsed=parsed,
        intervals=intervals,
        dedup=dedup,
        sensor_id=sensor_id,
        timezone=timezone,
        expected_interval_s=expected_interval_s,
        issues=issues,
        already_imported=already,
        checked_against_existing=checked,
    )


# ---------------------------------------------------------------------------
# Durchführung
# ---------------------------------------------------------------------------
def commit_import(
    repo: Any,
    preview: ImportPreview,
    *,
    sensor_id: str,
    room_id: str,
    progress: ProgressCallback | None = None,
    batch_size: int = 5000,
) -> ImportRecord:
    """Führt den Import durch und gibt das abgeschlossene Importprotokoll zurück.

    Protokoll-Zahlen: ``rows_read = rows_new + rows_duplicate + rows_invalid + rows_conflict``.
    ``rows_duplicate`` umfasst bereits gespeicherte identische Messungen, identische
    Doppelzeilen in der Datei und Zeilen, die die Datenbank als bereits vorhanden übersprang.
    ``rows_conflict`` umfasst Konflikte mit gespeicherten Werten und widersprüchliche Zeilen
    innerhalb der Datei.

    Raises:
        ImportBlockedError: Vorschau enthält ERROR-Meldungen.
        ImportCommitError: Fehler beim Speichern (Messungen wurden zurückgerollt).
    """
    if not preview.can_import:
        raise ImportBlockedError("Der Import ist nicht möglich, weil die Datei Fehler enthält.")
    if not sensor_id:
        raise ImportBlockedError("Bitte zuerst einen Sensor für diese Datei auswählen.")

    def report(fraction: float, text: str) -> None:
        if progress is not None:
            progress(max(0.0, min(1.0, fraction)), text)

    parsed = preview.parsed
    data = parsed.data
    record = ImportRecord(
        id=new_id(),
        sensor_id=sensor_id,
        room_id=room_id,
        file_name=parsed.file_name,
        file_sha256=parsed.sha256,
        status=ImportStatus.PENDING,
        rows_read=parsed.rows_read,
        rows_invalid=len(parsed.invalid_rows),
        ts_min=_ts_to_datetime(data[TS].min()),
        ts_max=_ts_to_datetime(data[TS].max()),
        expected_interval_s=preview.intervals.expected_s,
        observed_interval_s=preview.intervals.detected_s,
    )
    record = repo.create_import(record)
    try:
        report(0.0, "Vorhandene Messungen werden geprüft …")
        existing = _load_existing(repo, sensor_id, data)
        dedup = classify(data, existing, compare_metrics=parsed.metrics_present or None)
        new = dedup.new.reset_index(drop=True)
        cols = [TS, *METRICS] + ([RAW_TIMESTAMP] if RAW_TIMESTAMP in new.columns else [])
        new = new[cols]
        total = len(new)
        inserted = 0
        for start in range(0, total, max(int(batch_size), 1)):
            batch = new.iloc[start : start + batch_size]
            inserted += int(repo.insert_measurements(sensor_id, record.id, batch))
            done = start + len(batch)
            report(
                0.05 + 0.85 * done / total, f"{done:,} von {total:,} Messungen gespeichert".replace(",", ".")
            )
        if total == 0:
            report(0.9, "Keine neuen Messungen zu speichern.")

        if dedup.conflicts:
            repo.add_conflicts(
                [
                    MeasurementConflict(
                        id=new_id(),
                        sensor_id=sensor_id,
                        import_id=record.id,
                        ts=_ts_to_datetime(c.ts),  # type: ignore[arg-type]
                        existing=c.existing,
                        incoming=c.incoming,
                    )
                    for c in dedup.conflicts
                ]
            )

        counts = dedup.counts
        record.rows_new = inserted
        record.rows_duplicate = counts["duplicate"] + counts["in_file_duplicates"] + (total - inserted)
        record.rows_conflict = counts["conflict"] + counts["in_file_conflict_rows"]
        issues = list(preview.issues)
        # Duplikat-/Konfliktmeldungen der Vorschau durch die aktuelle Klassifikation ersetzen
        dedup_codes = {
            "in_file_duplicates",
            "in_file_conflicts",
            "already_present",
            "conflicts",
            "nothing_new",
        }
        issues = [i for i in issues if i.code not in dedup_codes] + dedup_issues(dedup, True)
        record.warnings = [i.message for i in issues if i.severity in (Severity.WARNING, Severity.ERROR)]
        record.details = {
            "detection": parsed.detection,
            "intervals": preview.intervals.to_dict(),
            "issues": [i.to_dict() for i in issues],
            "dedup": dedup.to_dict(),
            "empty_lines": parsed.empty_lines,
            "invalid_rows": parsed.to_dict(max_invalid=50)["invalid_rows"],
            "timezone": preview.timezone,
        }
        record.status = ImportStatus.COMPLETED
        record.finished_at = utcnow()
        record = repo.update_import(record)
        report(1.0, "Import abgeschlossen.")
        return record
    except Exception as exc:
        rolled_back = False
        try:
            repo.delete_measurements_of_import(record.id)
            repo.delete_conflicts_of_import(record.id)
            rolled_back = True
        except Exception:  # noqa: BLE001 – Rollback-Fehler darf den Originalfehler nicht verdecken
            rolled_back = False
        record.status = ImportStatus.ROLLED_BACK if rolled_back else ImportStatus.FAILED
        record.rows_new = 0
        record.finished_at = utcnow()
        text = (
            "Beim Speichern ist ein Fehler aufgetreten. Der Import wurde vollständig rückgängig "
            "gemacht; es wurden keine Messungen übernommen."
            if rolled_back
            else "Beim Speichern ist ein Fehler aufgetreten, und das Rückgängigmachen ist ebenfalls "
            "fehlgeschlagen. Bitte das Importprotokoll prüfen."
        )
        record.warnings = [*record.warnings, text]
        record.details = {**record.details, "error": f"{type(exc).__name__}: {exc}"}
        try:
            record = repo.update_import(record)
        except Exception:  # noqa: BLE001
            pass
        report(1.0, "Import fehlgeschlagen.")
        raise ImportCommitError(text, record) from exc


# ---------------------------------------------------------------------------
# Mehrere Dateien in einer Sitzung
# ---------------------------------------------------------------------------
@dataclass
class FileAssignment:
    """Zuordnung einer Datei zu genau einem Ziel-Sensor (für Mehrfach-Uploads)."""

    file_name: str
    sensor_id: str | None
    ts_min: pd.Timestamp | None
    ts_max: pd.Timestamp | None
    sha256: str | None = None

    @classmethod
    def from_preview(cls, preview: ImportPreview, sensor_id: str | None) -> FileAssignment:
        data = preview.parsed.data
        return cls(
            file_name=preview.file_name,
            sensor_id=sensor_id,
            ts_min=data[TS].min() if len(data) else None,
            ts_max=data[TS].max() if len(data) else None,
            sha256=preview.sha256,
        )


def check_file_assignments(files: Sequence[FileAssignment]) -> list[Issue]:
    """Prüft mehrere Dateien einer Import-Sitzung.

    * Datei ohne Ziel-Sensor → ERROR (jede Datei braucht genau einen Sensor).
    * Dieselbe Datei (gleicher Inhalt) mehrfach ausgewählt → WARNING.
    * Mehrere Dateien für denselben Sensor mit überlappendem Zeitraum → INFO (Überschneidungen
      werden beim Import als Duplikate/Konflikte erkannt).
    """
    issues: list[Issue] = []
    missing = [f.file_name for f in files if not f.sensor_id]
    if missing:
        issues.append(
            Issue(
                Severity.ERROR,
                "file_without_sensor",
                "Jede Datei muss genau einem Sensor zugeordnet werden.",
                len(missing),
                missing,
            )
        )
    seen: dict[str, str] = {}
    for f in files:
        if f.sha256 and f.sha256 in seen:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "same_file_twice",
                    f"„{f.file_name}“ hat denselben Inhalt wie „{seen[f.sha256]}“.",
                    1,
                    [f.file_name, seen[f.sha256]],
                )
            )
        elif f.sha256:
            seen[f.sha256] = f.file_name
    by_sensor: dict[str, list[FileAssignment]] = {}
    for f in files:
        if f.sensor_id and f.ts_min is not None and f.ts_max is not None:
            by_sensor.setdefault(f.sensor_id, []).append(f)
    for group in by_sensor.values():
        group = sorted(group, key=lambda f: f.ts_min)
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if b.ts_min <= a.ts_max:
                    issues.append(
                        Issue(
                            Severity.INFO,
                            "files_overlap",
                            f"„{a.file_name}“ und „{b.file_name}“ gehören zum selben Sensor und überschneiden "
                            "sich zeitlich. Doppelte Messungen werden nur einmal gespeichert.",
                            1,
                            [a.file_name, b.file_name],
                        )
                    )
    return issues
