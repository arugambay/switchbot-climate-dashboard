"""Tests für SqliteRepository (nur synthetische Daten)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.domain.models import (
    HUMIDITY,
    MEASUREMENT_COLUMNS,
    SENSOR_ID,
    TEMPERATURE,
    TS,
    ConflictResolution,
    ImportRecord,
    ImportStatus,
    MeasurementConflict,
    new_id,
)
from climate_dashboard.reports.sharing import generate_token, hash_token, resolve_shared_report, token_hint
from climate_dashboard.storage.base import IntegrityError, NotFoundError, Repository, RepositoryError
from climate_dashboard.storage.sqlite_repo import SCHEMA_VERSION, SqliteRepository
from tests.synthetic import make_measurements

SHA = "a" * 64


@pytest.fixture
def repo():
    r = SqliteRepository(":memory:")
    yield r
    r.close()


@pytest.fixture
def setup(repo):
    room = repo.create_room("Schlafzimmer", "Nordseite")
    sensor = repo.create_sensor(room.id, "Hygrometer 1", expected_interval_s=1800)
    imp = repo.create_import(
        ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="a.csv", file_sha256=SHA)
    )
    return repo, room, sensor, imp


def _utc(*args):
    return datetime(*args, tzinfo=UTC)


# --- Grundlagen -----------------------------------------------------------------
def test_implements_protocol_and_schema_version(repo):
    assert isinstance(repo, Repository)
    assert repo.schema_version == SCHEMA_VERSION == 1


def test_file_db_uses_wal_and_foreign_keys(tmp_path):
    path = tmp_path / "sub" / "dev.sqlite"
    r = SqliteRepository(path)
    assert path.exists()
    assert r._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert r._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    r.close()
    # erneutes Öffnen migriert nicht doppelt
    r2 = SqliteRepository(path)
    assert r2.schema_version == 1
    r2.close()


# --- Räume / Sensoren ------------------------------------------------------------
def test_room_crud(repo):
    room = repo.create_room("  Bad  ", "mit Fenster")
    assert room.name == "Bad"
    assert repo.get_room(room.id).description == "mit Fenster"
    assert [r.name for r in repo.list_rooms()] == ["Bad"]
    updated = repo.update_room(room.id, description="ohne Fenster")
    assert updated.description == "ohne Fenster"
    with pytest.raises(NotFoundError):
        repo.get_room(new_id())
    with pytest.raises(IntegrityError):
        repo.create_room("   ")


def test_duplicate_active_room_name_rejected(repo):
    repo.create_room("Küche")
    with pytest.raises(IntegrityError):
        repo.create_room("  küche ")
    other = repo.create_room("Flur")
    with pytest.raises(IntegrityError):
        repo.rename_room(other.id, "KÜCHE")
    assert repo.get_room(other.id).name == "Flur"


def test_archived_room_frees_name_and_unarchive_checks(repo):
    old = repo.create_room("Keller")
    archived = repo.archive_room(old.id)
    assert archived.is_archived
    assert repo.list_rooms() == []
    assert [r.id for r in repo.list_rooms(include_archived=True)] == [old.id]
    new = repo.create_room("Keller")
    with pytest.raises(IntegrityError):
        repo.unarchive_room(old.id)
    repo.rename_room(new.id, "Keller neu")
    assert repo.unarchive_room(old.id).archived_at is None


def test_rename_keeps_ids_and_measurement_links(setup):
    repo, room, sensor, imp = setup
    df = make_measurements(end="2026-03-02 00:00")
    assert repo.insert_measurements(sensor.id, imp.id, df) == 48
    renamed = repo.rename_room(room.id, "Gästezimmer")
    assert renamed.id == room.id and renamed.name == "Gästezimmer"
    s2 = repo.update_sensor(sensor.id, name="Neuer Name")
    assert s2.id == sensor.id and s2.room_id == room.id
    loaded = repo.load_measurements([sensor.id])
    assert len(loaded) == 48
    assert set(loaded[SENSOR_ID]) == {sensor.id}
    assert repo.list_imports(room_id=room.id)[0].id == imp.id


def test_sensor_crud_and_move(repo):
    a = repo.create_room("A")
    b = repo.create_room("B")
    s = repo.create_sensor(a.id, "S1", device_info="Meter Pro", timezone="Europe/Berlin")
    assert repo.get_sensor(s.id).device_info == "Meter Pro"
    moved = repo.update_sensor(s.id, room_id=b.id, expected_interval_s=600)
    assert moved.room_id == b.id and moved.expected_interval_s == 600
    assert repo.list_sensors(a.id) == []
    assert [x.id for x in repo.list_sensors(b.id)] == [s.id]
    with pytest.raises(RepositoryError):
        repo.update_sensor(s.id, id="hack")
    with pytest.raises(NotFoundError):
        repo.update_sensor(s.id, room_id=new_id())
    with pytest.raises(NotFoundError):
        repo.create_sensor(new_id(), "X")
    assert repo.archive_sensor(s.id).is_archived
    assert repo.list_sensors(b.id) == []
    assert len(repo.list_sensors(b.id, include_archived=True)) == 1
    assert not repo.unarchive_sensor(s.id).is_archived


def test_delete_room_cascades(setup):
    repo, room, sensor, imp = setup
    repo.insert_measurements(sensor.id, imp.id, make_measurements(end="2026-03-01 12:00"))
    rep = repo.create_report(room.id, "Bericht")
    repo.add_report_version(
        rep.id,
        period_start=_utc(2026, 3, 1),
        period_end=_utc(2026, 3, 2),
        config={},
        content={"a": 1},
        data_fingerprint="fp",
    )
    repo.create_share(rep.id, token_hash=hash_token(generate_token()), token_hint="abcd")
    repo.add_conflicts(
        [
            MeasurementConflict(
                id=new_id(),
                sensor_id=sensor.id,
                import_id=imp.id,
                ts=_utc(2026, 2, 28, 23),
                existing={TEMPERATURE: 20.0},
                incoming={TEMPERATURE: 21.0},
            )
        ]
    )
    repo.delete_room(room.id)
    for table in (
        "rooms",
        "sensors",
        "measurements",
        "imports",
        "measurement_conflicts",
        "reports",
        "report_versions",
        "shares",
    ):
        assert repo._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    with pytest.raises(NotFoundError):
        repo.delete_room(room.id)


# --- Messungen --------------------------------------------------------------------
def test_insert_twice_inserts_zero_second_time(setup):
    repo, room, sensor, imp = setup
    df = make_measurements()  # 7 Tage × 48
    assert repo.insert_measurements(sensor.id, imp.id, df) == 336
    imp2 = repo.create_import(
        ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="b.csv", file_sha256=SHA)
    )
    assert repo.insert_measurements(sensor.id, imp2.id, df) == 0
    assert repo.measurement_extent(sensor.id)[2] == 336


def test_roundtrip_values_types_and_raw_timestamp(setup):
    repo, _, sensor, imp = setup
    df = make_measurements(end="2026-03-01 02:00")
    df.loc[1, HUMIDITY] = np.nan
    df["raw_timestamp"] = ["2026-03-01 00:00:00", "2026-03-01 00:30:00", None, "x"]
    repo.insert_measurements(sensor.id, imp.id, df)
    out = repo.load_measurements([sensor.id])
    assert list(out.columns) == [SENSOR_ID, *MEASUREMENT_COLUMNS]
    assert str(out[TS].dtype) == "datetime64[ns, UTC]"
    pd.testing.assert_series_equal(out[TS], df[TS].astype("datetime64[ns, UTC]"), check_names=False)
    assert out[TEMPERATURE].tolist() == df[TEMPERATURE].tolist()
    assert np.isnan(out.loc[1, HUMIDITY])
    raw = repo.load_measurements([sensor.id], include_raw=True)["raw_timestamp"].tolist()
    assert raw == ["2026-03-01 00:00:00", "2026-03-01 00:30:00", None, "x"]


def test_range_query_is_half_open(setup):
    repo, _, sensor, imp = setup
    repo.insert_measurements(sensor.id, imp.id, make_measurements(tz="UTC"))
    start, end = _utc(2026, 3, 2, 0, 0), _utc(2026, 3, 2, 1, 0)
    out = repo.load_measurements([sensor.id], start, end)
    assert out[TS].tolist() == [
        pd.Timestamp("2026-03-02 00:00", tz="UTC"),
        pd.Timestamp("2026-03-02 00:30", tz="UTC"),
    ]
    assert len(repo.load_measurements([sensor.id], start=_utc(2026, 3, 7, 23, 0))) == 2
    assert len(repo.load_measurements([sensor.id], end=_utc(2026, 3, 1, 0, 30))) == 1
    empty = repo.load_measurements([sensor.id], _utc(2030, 1, 1), _utc(2030, 1, 2))
    assert empty.empty and list(empty.columns) == [SENSOR_ID, *MEASUREMENT_COLUMNS]
    assert repo.load_measurements([]).empty


def test_multi_sensor_load_sorted(setup):
    repo, room, s1, imp = setup
    s2 = repo.create_sensor(room.id, "S2")
    imp2 = repo.create_import(
        ImportRecord(id=new_id(), sensor_id=s2.id, room_id=room.id, file_name="c.csv", file_sha256=SHA)
    )
    repo.insert_measurements(s1.id, imp.id, make_measurements(end="2026-03-01 02:00"))
    repo.insert_measurements(s2.id, imp2.id, make_measurements(end="2026-03-01 02:00", seed=2))
    out = repo.load_measurements([s1.id, s2.id])
    assert len(out) == 8
    assert out[TS].is_monotonic_increasing


def test_extent(setup):
    repo, _, sensor, imp = setup
    assert repo.measurement_extent(sensor.id) == (None, None, 0)
    repo.insert_measurements(sensor.id, imp.id, make_measurements(tz="UTC"))
    first, last, n = repo.measurement_extent(sensor.id)
    assert first == _utc(2026, 3, 1) and last == _utc(2026, 3, 7, 23, 30) and n == 336


def test_naive_timestamps_rejected(setup):
    repo, _, sensor, imp = setup
    df = make_measurements(end="2026-03-01 01:00")
    df[TS] = df[TS].dt.tz_localize(None)
    with pytest.raises(RepositoryError):
        repo.insert_measurements(sensor.id, imp.id, df)


def test_check_constraint_rolls_back_whole_batch(setup):
    repo, _, sensor, imp = setup
    df = make_measurements(end="2026-03-01 02:00")
    df.loc[3, HUMIDITY] = 140.0
    with pytest.raises(IntegrityError):
        repo.insert_measurements(sensor.id, imp.id, df)
    assert repo.measurement_extent(sensor.id)[2] == 0


def test_insert_requires_existing_import(setup):
    repo, _, sensor, _ = setup
    with pytest.raises(IntegrityError):
        repo.insert_measurements(sensor.id, new_id(), make_measurements(end="2026-03-01 01:00"))


def test_delete_measurements_of_import(setup):
    repo, room, sensor, imp = setup
    repo.insert_measurements(sensor.id, imp.id, make_measurements(end="2026-03-02 00:00"))  # 48
    imp2 = repo.create_import(
        ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="b.csv", file_sha256=SHA)
    )
    # überlappend: nur die 48 neuen Zeilen gehören zu imp2
    assert repo.insert_measurements(sensor.id, imp2.id, make_measurements(end="2026-03-03 00:00")) == 48
    assert repo.delete_measurements_of_import(imp2.id) == 48
    assert repo.measurement_extent(sensor.id)[2] == 48
    assert repo.delete_measurements_of_import(imp2.id) == 0


# --- Importe -----------------------------------------------------------------------
def test_import_roundtrip(setup):
    repo, room, sensor, imp = setup
    imp.status = ImportStatus.COMPLETED
    imp.rows_read, imp.rows_new = 10, 8
    imp.ts_min, imp.ts_max = _utc(2026, 3, 1), _utc(2026, 3, 2)
    imp.warnings = ["Lücke erkannt"]
    imp.details = {"delimiter": ","}
    imp.finished_at = _utc(2026, 3, 3)
    got = repo.update_import(imp)
    assert got.status is ImportStatus.COMPLETED
    assert (got.rows_read, got.rows_new) == (10, 8)
    assert got.warnings == ["Lücke erkannt"] and got.details == {"delimiter": ","}
    assert got.ts_max == _utc(2026, 3, 2)
    assert [i.id for i in repo.find_imports_by_hash(sensor.id, SHA)] == [imp.id]
    assert repo.find_imports_by_hash(sensor.id, "b" * 64) == []
    assert len(repo.list_imports(sensor_id=sensor.id)) == 1
    with pytest.raises(NotFoundError):
        repo.get_import(new_id())


# --- Konflikte ---------------------------------------------------------------------
def _conflict_setup(setup):
    repo, room, sensor, imp = setup
    df = make_measurements(end="2026-03-01 01:00", tz="UTC")
    repo.insert_measurements(sensor.id, imp.id, df)
    ts = _utc(2026, 3, 1, 0, 30)
    existing = {m: float(df.loc[1, m]) for m in df.columns if m != TS}
    incoming = {**existing, TEMPERATURE: 25.5, HUMIDITY: 70.0}
    c = MeasurementConflict(
        id=new_id(), sensor_id=sensor.id, import_id=imp.id, ts=ts, existing=existing, incoming=incoming
    )
    repo.add_conflicts([c])
    return repo, sensor, c, existing


def test_conflict_use_incoming_replaces_values_keeps_existing(setup):
    repo, sensor, c, existing = _conflict_setup(setup)
    assert [x.id for x in repo.list_conflicts(sensor.id)] == [c.id]
    resolved = repo.resolve_conflict(c.id, ConflictResolution.USE_INCOMING)
    assert resolved.resolution is ConflictResolution.USE_INCOMING
    assert resolved.resolved_at is not None
    assert resolved.existing == existing
    row = repo.load_measurements([sensor.id], _utc(2026, 3, 1, 0, 30), _utc(2026, 3, 1, 0, 31))
    assert row[TEMPERATURE].iloc[0] == 25.5 and row[HUMIDITY].iloc[0] == 70.0
    assert repo.list_conflicts(sensor.id) == []
    assert len(repo.list_conflicts(sensor.id, unresolved_only=False)) == 1
    with pytest.raises(IntegrityError):
        repo.resolve_conflict(c.id, ConflictResolution.KEEP_EXISTING)


def test_delete_conflicts_of_import(setup):
    repo, sensor, c, _ = _conflict_setup(setup)
    assert repo.delete_conflicts_of_import(new_id()) == 0
    assert repo.delete_conflicts_of_import(c.import_id) == 1
    assert repo.list_conflicts(sensor.id, unresolved_only=False) == []
    assert repo.delete_conflicts_of_import(c.import_id) == 0


def test_raw_timestamp_is_stored(setup):
    repo, _, sensor, imp = setup
    df = make_measurements(end="2026-03-01 01:00")
    df["raw_timestamp"] = [f"roh-{i}" for i in range(len(df))]
    repo.insert_measurements(sensor.id, imp.id, df)
    got = repo.load_measurements([sensor.id], include_raw=True)
    assert got["raw_timestamp"].tolist() == df["raw_timestamp"].tolist()


def test_conflict_keep_existing_changes_nothing(setup):
    repo, sensor, c, existing = _conflict_setup(setup)
    before = repo.load_measurements([sensor.id])
    resolved = repo.resolve_conflict(c.id, ConflictResolution.KEEP_EXISTING)
    assert resolved.resolution is ConflictResolution.KEEP_EXISTING
    pd.testing.assert_frame_equal(before, repo.load_measurements([sensor.id]))


def test_replace_measurement_values_missing_row(setup):
    repo, _, sensor, _ = setup
    with pytest.raises(NotFoundError):
        repo.replace_measurement_values(sensor.id, _utc(2000, 1, 1), {TEMPERATURE: 1.0})


# --- Berichte -----------------------------------------------------------------------
def _version(repo, report_id, n=1, make_current=True):
    return repo.add_report_version(
        report_id,
        period_start=_utc(2026, 3, 1),
        period_end=_utc(2026, 4, 1),
        config={"metric": "humidity"},
        content={"schema_version": 1, "n": n, "text": "Übersicht"},
        data_fingerprint=f"fp{n}",
        make_current=make_current,
    )


def test_report_versions_sequential_and_immutable(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "März")
    v1 = _version(repo, rep.id, 1)
    v2 = _version(repo, rep.id, 2)
    v3 = _version(repo, rep.id, 3, make_current=False)
    assert [v.version for v in repo.list_report_versions(rep.id)] == [1, 2, 3]
    assert repo.get_report(rep.id).current_version_id == v2.id
    assert repo.get_report_version(v1.id).content == {"schema_version": 1, "n": 1, "text": "Übersicht"}
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute("UPDATE report_versions SET content = '{}' WHERE id = ?", (v1.id,))
    assert repo.set_current_version(rep.id, v1.id).current_version_id == v1.id
    other = repo.create_report(room.id, "Anderer")
    with pytest.raises(NotFoundError):
        repo.set_current_version(other.id, v3.id)
    # separate Nummerierung pro Bericht
    assert _version(repo, other.id).version == 1


def test_report_crud(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "Titel")
    assert repo.update_report(rep.id, title="Neu").title == "Neu"
    assert repo.archive_report(rep.id).archived_at is not None
    assert repo.list_reports(room.id) == []
    assert len(repo.list_reports(room.id, include_archived=True)) == 1
    repo.delete_report(rep.id)
    with pytest.raises(NotFoundError):
        repo.get_report(rep.id)


def test_nan_in_content_rejected(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "T")
    with pytest.raises(RepositoryError):
        repo.add_report_version(
            rep.id,
            period_start=_utc(2026, 3, 1),
            period_end=_utc(2026, 4, 1),
            config={},
            content={"x": float("nan")},
            data_fingerprint="fp",
        )


# --- Freigaben ----------------------------------------------------------------------
@pytest.fixture
def shared(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "Freigabe")
    _version(repo, rep.id, 1)
    token = generate_token()
    share = repo.create_share(
        rep.id, token_hash=hash_token(token), token_hint=token_hint(token), label="Vermieter"
    )
    return repo, rep, share, token


def test_valid_share_returns_only_content_and_counts(shared):
    repo, rep, share, token = shared
    result = repo.resolve_share(hash_token(token))
    assert set(result) == {"content", "version", "created_at", "expires_at"}
    assert result["content"]["text"] == "Übersicht" and result["version"] == 1
    assert result["expires_at"] is None
    flat = repr(result)
    for internal in (rep.id, rep.room_id, share.id, share.token_hash, "report_id", "room_id", "owner"):
        assert internal not in flat
    resolve_shared_report(repo, token)
    s = repo.list_shares(rep.id)[0]
    assert s.access_count == 2 and s.last_accessed_at is not None


def test_share_shows_current_version(shared):
    repo, rep, _, token = shared
    _version(repo, rep.id, 2)
    assert repo.resolve_share(hash_token(token))["version"] == 2


def test_revoked_share_returns_none(shared):
    repo, _, share, token = shared
    revoked = repo.revoke_share(share.id)
    assert revoked.revoked_at is not None
    assert repo.resolve_share(hash_token(token)) is None
    assert repo.list_shares()[0].access_count == 0


def test_expired_share_returns_none(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "R")
    _version(repo, rep.id)
    token = generate_token()
    repo.create_share(
        rep.id,
        token_hash=hash_token(token),
        token_hint="x",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert repo.resolve_share(hash_token(token)) is None
    token2 = generate_token()
    future = datetime.now(UTC) + timedelta(days=7)
    repo.create_share(rep.id, token_hash=hash_token(token2), token_hint="y", expires_at=future)
    assert repo.resolve_share(hash_token(token2))["expires_at"] == future.isoformat()


def test_wrong_token_returns_none(shared):
    repo, *_ = shared
    assert repo.resolve_share(hash_token(generate_token())) is None
    assert repo.resolve_share("not-a-hash") is None
    assert resolve_shared_report(repo, "kurz") is None


def test_archived_or_deleted_report_returns_none(shared):
    repo, rep, _, token = shared
    repo.archive_report(rep.id)
    assert repo.resolve_share(hash_token(token)) is None
    repo.delete_report(rep.id)
    assert repo.resolve_share(hash_token(token)) is None
    assert repo.list_shares() == []


def test_report_without_version_not_shared(setup):
    repo, room, *_ = setup
    rep = repo.create_report(room.id, "Leer")
    token = generate_token()
    repo.create_share(rep.id, token_hash=hash_token(token), token_hint="z")
    assert repo.resolve_share(hash_token(token)) is None


def test_plaintext_token_never_stored(tmp_path):
    path = tmp_path / "t.sqlite"
    repo = SqliteRepository(path)
    room = repo.create_room("R")
    rep = repo.create_report(room.id, "B")
    _version(repo, rep.id)
    token = generate_token()
    with pytest.raises(IntegrityError):  # Klartext statt Hash wird abgelehnt
        repo.create_share(rep.id, token_hash=token, token_hint=token_hint(token))
    repo.create_share(rep.id, token_hash=hash_token(token), token_hint=token_hint(token))
    assert repo.resolve_share_token(token) is not None
    repo._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    repo.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert token.encode() not in blob
    assert hash_token(token).encode() in blob


def test_duplicate_token_hash_rejected(shared):
    repo, rep, share, _ = shared
    with pytest.raises(IntegrityError):
        repo.create_share(rep.id, token_hash=share.token_hash, token_hint="dup")


# --- Einstellungen ------------------------------------------------------------------
def test_settings_roundtrip(repo):
    assert repo.get_settings() == {}
    repo.save_settings({"humidity_high": 70.0, "timezone": "Europe/Berlin", "surface_offset_k": None})
    assert repo.get_settings() == {
        "humidity_high": 70.0,
        "timezone": "Europe/Berlin",
        "surface_offset_k": None,
    }
    repo.save_settings({"humidity_high": 65.0})
    assert repo.get_settings() == {"humidity_high": 65.0}


def test_moving_sensor_moves_its_import_logs(setup):
    repo, room, sensor, imp = setup
    other = repo.create_room("Zielraum")
    repo.update_sensor(sensor.id, room_id=other.id)
    assert [i.id for i in repo.list_imports(room_id=other.id)] == [imp.id]
    assert repo.list_imports(room_id=room.id) == []
