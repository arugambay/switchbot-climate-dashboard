"""Tests für Importvorschau, Import, Protokoll und Rollback (In-Memory-Repository)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from climate_dashboard.domain.models import TEMPERATURE, TS, ImportStatus
from climate_dashboard.ingest.service import (
    FileAssignment,
    ImportBlockedError,
    ImportCommitError,
    check_file_assignments,
    commit_import,
    preview_import,
)
from climate_dashboard.ingest.validation import Severity
from tests.fake_repo import FakeRepository
from tests.synthetic import make_measurements, to_switchbot_csv


@pytest.fixture
def repo_and_sensor():
    repo = FakeRepository()
    room = repo.create_room("Testraum")
    sensor = repo.create_sensor(room.id, "Testsensor")
    return repo, room, sensor


def week(start="2026-01-05", end="2026-01-12", **kw) -> pd.DataFrame:
    return make_measurements(start, end, interval_min=30, **kw)


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def assert_protocol_sums(rec):
    assert rec.rows_read == rec.rows_new + rec.rows_duplicate + rec.rows_invalid + rec.rows_conflict


def test_preview_without_sensor(repo_and_sensor):
    repo, _, _ = repo_and_sensor
    content = to_switchbot_csv(week())
    p = preview_import(repo, content, "Raum_export.csv", sensor_id=None)
    assert p.can_import
    assert p.checked_against_existing is False
    assert repo.load_calls == []
    assert p.counts["rows_read"] == 336 and p.counts["rows_new"] == 336
    assert p.intervals.expected_s == 1800
    json.dumps(p.to_dict())


def test_full_import_and_protocol(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    df = week()
    content = to_switchbot_csv(df)
    p = preview_import(repo, content, "a.csv", sensor_id=sensor.id)
    events = []
    rec = commit_import(
        repo,
        p,
        sensor_id=sensor.id,
        room_id=room.id,
        batch_size=100,
        progress=lambda f, t: events.append((f, t)),
    )
    assert rec.status == ImportStatus.COMPLETED
    assert (rec.rows_read, rec.rows_new, rec.rows_duplicate, rec.rows_invalid, rec.rows_conflict) == (
        336,
        336,
        0,
        0,
        0,
    )
    assert rec.ts_min == df[TS].min().to_pydatetime() and rec.ts_max == df[TS].max().to_pydatetime()
    assert rec.expected_interval_s == 1800 and rec.observed_interval_s == 1800
    assert rec.finished_at is not None
    assert rec.details["detection"]["delimiter"] == ","
    assert repo.count_measurements(sensor.id) == 336
    assert repo.insert_calls == 4  # 336 Zeilen in Batches à 100
    fractions = [f for f, _ in events]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    stored = repo.get_import(rec.id)
    assert stored.status == ImportStatus.COMPLETED and stored.rows_new == 336
    json.dumps(stored.details)


def test_double_import_creates_no_new_rows(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    content = to_switchbot_csv(week())
    commit_import(
        repo,
        preview_import(repo, content, "a.csv", sensor_id=sensor.id),
        sensor_id=sensor.id,
        room_id=room.id,
    )
    p2 = preview_import(repo, content, "a.csv", sensor_id=sensor.id)
    assert "file_already_imported" in codes(p2.issues, Severity.WARNING)
    assert "nothing_new" in codes(p2.issues, Severity.INFO)
    assert len(p2.already_imported) == 1
    assert p2.can_import
    rec2 = commit_import(repo, p2, sensor_id=sensor.id, room_id=room.id)
    assert (rec2.rows_new, rec2.rows_duplicate) == (0, 336)
    assert repo.count_measurements(sensor.id) == 336
    assert_protocol_sums(rec2)


def test_preview_loads_only_affected_range(repo_and_sensor):
    repo, _, sensor = repo_and_sensor
    df = week()
    preview_import(repo, to_switchbot_csv(df), "a.csv", sensor_id=sensor.id)
    ((ids, start, end),) = repo.load_calls
    assert ids == [sensor.id]
    assert pd.Timestamp(start) == df[TS].min()
    assert pd.Timestamp(end) == df[TS].max() + pd.Timedelta(seconds=1)


def test_overlap_conflicts_invalid_and_in_file_duplicates(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    df = week()
    # Erster Import: erste Hälfte, aber mit 2 abweichenden Temperaturen
    first = df.iloc[:168].copy()
    first.loc[[10, 20], TEMPERATURE] += 1.0
    commit_import(
        repo,
        preview_import(repo, to_switchbot_csv(first), "a.csv", sensor_id=sensor.id),
        sensor_id=sensor.id,
        room_id=room.id,
    )
    # Zweite Datei: alles + 1 doppelte Zeile + 2 ungültige Zeilen
    text = to_switchbot_csv(df).decode()
    lines = text.splitlines()
    lines.insert(50, lines[49])  # identische Doppelzeile
    lines.append('kein-datum,"1,0",50,"1,0","1,0","1,0"')
    lines.append('2026-01-20 10:00:00,"200,0",150,"1,0","1,0","1,0"')
    content = ("\n".join(lines) + "\n").encode()
    p = preview_import(repo, content, "b.csv", sensor_id=sensor.id)
    assert p.counts == {
        "rows_read": 339,
        "rows_valid": 337,
        "rows_invalid": 2,
        "rows_new": 168,
        "rows_duplicate": 166 + 1,
        "rows_conflict": 2,
        "empty_lines": 0,
    }
    assert {"conflicts", "already_present", "in_file_duplicates"} <= codes(p.issues)
    rec = commit_import(repo, p, sensor_id=sensor.id, room_id=room.id)
    assert (rec.rows_read, rec.rows_new, rec.rows_duplicate, rec.rows_invalid, rec.rows_conflict) == (
        339,
        168,
        167,
        2,
        2,
    )
    assert_protocol_sums(rec)
    conflicts = repo.list_conflicts(sensor.id)
    assert len(conflicts) == 2 and all(c.import_id == rec.id for c in conflicts)
    c = sorted(conflicts, key=lambda c: c.ts)[0]
    assert c.ts == df[TS].iloc[10].to_pydatetime()
    assert c.existing[TEMPERATURE] == pytest.approx(df[TEMPERATURE].iloc[10] + 1.0)
    assert c.incoming[TEMPERATURE] == pytest.approx(df[TEMPERATURE].iloc[10])
    # Vorhandene Werte wurden nicht überschrieben
    stored = repo.load_measurements([sensor.id]).set_index(TS)
    assert stored.loc[df[TS].iloc[10], TEMPERATURE] == pytest.approx(df[TEMPERATURE].iloc[10] + 1.0)
    assert repo.count_measurements(sensor.id) == 336


def test_in_file_conflict_counts(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    content = (
        b"timestamp,temperature,humidity\n"
        b"2026-01-05 10:00,20,50\n"
        b"2026-01-05 10:30,21,50\n"
        b"2026-01-05 10:30,22,50\n"
        b"2026-01-05 11:00,23,50\n"
    )
    p = preview_import(repo, content, "c.csv", sensor_id=sensor.id)
    assert "in_file_conflicts" in codes(p.issues, Severity.WARNING)
    rec = commit_import(repo, p, sensor_id=sensor.id, room_id=room.id)
    assert (rec.rows_new, rec.rows_conflict) == (2, 2)
    assert repo.list_conflicts(sensor.id) == []  # Datei-interne Konflikte werden nur gemeldet
    assert_protocol_sums(rec)


def test_reclassification_before_write_handles_race(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    df = week()
    content = to_switchbot_csv(df)
    p = preview_import(repo, content, "a.csv", sensor_id=sensor.id)
    assert p.counts["rows_new"] == 336
    # Inzwischen importiert jemand anderes die erste Hälfte
    commit_import(
        repo,
        preview_import(repo, to_switchbot_csv(df.iloc[:100]), "x.csv", sensor_id=sensor.id),
        sensor_id=sensor.id,
        room_id=room.id,
    )
    rec = commit_import(repo, p, sensor_id=sensor.id, room_id=room.id)
    assert (rec.rows_new, rec.rows_duplicate) == (236, 100)
    assert repo.count_measurements(sensor.id) == 336


def test_rollback_on_repository_error(repo_and_sensor):
    _, _, _ = repo_and_sensor
    repo = FakeRepository(fail_after_batches=1)
    room = repo.create_room("R")
    sensor = repo.create_sensor(room.id, "S")
    p = preview_import(repo, to_switchbot_csv(week()), "a.csv", sensor_id=sensor.id)
    with pytest.raises(ImportCommitError) as excinfo:
        commit_import(repo, p, sensor_id=sensor.id, room_id=room.id, batch_size=100)
    assert "rückgängig" in str(excinfo.value)
    assert repo.insert_calls == 1  # erster Batch war bereits geschrieben …
    assert repo.count_measurements() == 0  # … und wurde zurückgerollt
    (record,) = repo.list_imports(sensor_id=sensor.id)
    assert record.status == ImportStatus.ROLLED_BACK
    assert record.rows_new == 0
    assert "Simulierter Datenbankfehler" in record.details["error"]
    assert excinfo.value.record is not None and excinfo.value.record.status == ImportStatus.ROLLED_BACK


def test_commit_blocked_on_errors(repo_and_sensor):
    repo, room, sensor = repo_and_sensor
    p = preview_import(repo, b"timestamp,humidity\n2026-01-01 10:00,50\n", "x.csv", sensor_id=sensor.id)
    assert not p.can_import
    with pytest.raises(ImportBlockedError):
        commit_import(repo, p, sensor_id=sensor.id, room_id=room.id)
    assert repo.list_imports() == []


def test_interval_issues_in_preview(repo_and_sensor):
    repo, _, sensor = repo_and_sensor
    df = week(gaps=[("2026-01-07 00:00", "2026-01-07 12:00")])
    p = preview_import(repo, to_switchbot_csv(df), "a.csv", sensor_id=sensor.id, expected_interval_s=900)
    assert "interval_deviates" in codes(p.issues, Severity.WARNING)
    assert "gaps" in codes(p.issues, Severity.INFO)
    assert p.intervals.expected_source == "user"


def test_file_assignments():
    t = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    files = [
        FileAssignment("a.csv", "s1", t("2026-01-01"), t("2026-01-10"), "h1"),
        FileAssignment("b.csv", "s1", t("2026-01-05"), t("2026-01-20"), "h2"),
        FileAssignment("c.csv", "s2", t("2026-01-05"), t("2026-01-20"), "h3"),
        FileAssignment("d.csv", None, t("2026-01-05"), t("2026-01-20"), "h4"),
        FileAssignment("e.csv", "s2", t("2026-02-05"), t("2026-02-20"), "h1"),
    ]
    issues = check_file_assignments(files)
    by_code = {i.code: i for i in issues}
    assert by_code["file_without_sensor"].severity == Severity.ERROR
    assert by_code["file_without_sensor"].examples == ["d.csv"]
    assert by_code["same_file_twice"].examples == ["e.csv", "a.csv"]
    overlaps = [i for i in issues if i.code == "files_overlap"]
    assert len(overlaps) == 1 and overlaps[0].examples == ["a.csv", "b.csv"]  # c/e: anderer Zeitraum


def test_file_assignment_from_preview(repo_and_sensor):
    repo, _, _ = repo_and_sensor
    p = preview_import(repo, to_switchbot_csv(week()), "a.csv", sensor_id=None)
    fa = FileAssignment.from_preview(p, "s1")
    assert fa.sha256 == p.sha256 and fa.ts_min == p.parsed.data[TS].min()


def test_rollback_also_removes_conflicts(repo_and_sensor, monkeypatch):
    repo, room, sensor = repo_and_sensor
    df = week()
    first = df.iloc[:50].copy()
    first.loc[[5], TEMPERATURE] += 1.0
    commit_import(
        repo,
        preview_import(repo, to_switchbot_csv(first), "a.csv", sensor_id=sensor.id),
        sensor_id=sensor.id,
        room_id=room.id,
    )
    p = preview_import(repo, to_switchbot_csv(df), "b.csv", sensor_id=sensor.id)
    original_update = repo.update_import

    def failing_update(record):
        if record.status == ImportStatus.COMPLETED:
            raise RuntimeError("Abschluss fehlgeschlagen")
        return original_update(record)

    monkeypatch.setattr(repo, "update_import", failing_update)
    with pytest.raises(ImportCommitError):
        commit_import(repo, p, sensor_id=sensor.id, room_id=room.id)
    assert repo.count_measurements(sensor.id) == 50  # nur der erste Import bleibt
    assert repo.list_conflicts(sensor.id) == []  # Konflikt des fehlgeschlagenen Imports entfernt
    failed = [r for r in repo.list_imports(sensor_id=sensor.id) if r.file_name == "b.csv"]
    assert failed[0].status == ImportStatus.ROLLED_BACK
