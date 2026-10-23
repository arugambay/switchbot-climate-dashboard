"""End-to-End: Daten → Snapshot → Version → Freigabe → öffentlicher Abruf (SqliteRepository)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from climate_dashboard.domain.models import AnalysisSettings, ImportRecord, new_id
from climate_dashboard.reports import sharing
from climate_dashboard.reports.builder import ReportOptions, build_snapshot, data_fingerprint
from climate_dashboard.storage.sqlite_repo import SqliteRepository
from tests.synthetic import make_measurements

START = datetime(2026, 3, 1, tzinfo=UTC)
END = datetime(2026, 3, 15, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    repo = SqliteRepository(tmp_path / "flow.sqlite")
    room = repo.create_room("Kinderzimmer Ost")
    sensor = repo.create_sensor(room.id, "Meter", expected_interval_s=1800)
    imp = repo.create_import(ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="a.csv",
                                          file_sha256="a" * 64))
    repo.insert_measurements(sensor.id, imp.id, make_measurements("2026-03-01", "2026-03-10", 30))
    yield repo, room, sensor
    repo.close()


def _version(repo, room, sensor, report_id=None):
    df = repo.load_measurements([sensor.id], START, END)
    fp = data_fingerprint(df, [sensor.id], START, END)
    if report_id is None:
        report_id = repo.create_report(room.id, "Bericht").id
    n = len(repo.list_report_versions(report_id)) + 1
    snap = build_snapshot(df, room=room, sensors=[sensor], period_start=START, period_end=END,
                          options=ReportOptions(title="Bericht"), settings=AnalysisSettings(),
                          expected_s=sensor.expected_interval_s, version=n)
    v = repo.add_report_version(report_id, period_start=START, period_end=END,
                                config={"sensor_ids": [sensor.id]}, content=snap, data_fingerprint=fp)
    return report_id, v


def _share(repo, report_id, **kw):
    token = sharing.generate_token()
    repo.create_share(report_id, token_hash=sharing.hash_token(token), token_hint=sharing.token_hint(token), **kw)
    return token


def test_full_flow(env):
    repo, room, sensor = env
    report_id, v1 = _version(repo, room, sensor)
    token = _share(repo, report_id, label="Vermieter")
    res = sharing.resolve_shared_report(repo, token)
    assert res["version"] == 1
    assert res["content"]["meta"]["room_name"] == "Kinderzimmer Ost"
    assert res["content"]["meta"]["version"] == 1
    flat = repr(res)
    for internal in (room.id, sensor.id, report_id):
        assert internal not in flat
    n1 = res["content"]["quality"]["n"]

    # Neue Messungen ändern die gespeicherte Version nicht, aber den Fingerprint
    imp = repo.create_import(ImportRecord(id=new_id(), sensor_id=sensor.id, room_id=room.id, file_name="b.csv",
                                          file_sha256="b" * 64))
    repo.insert_measurements(sensor.id, imp.id, make_measurements("2026-03-10", "2026-03-15", 30, seed=5))
    res2 = sharing.resolve_shared_report(repo, token)
    assert res2["version"] == 1 and res2["content"]["quality"]["n"] == n1
    assert repo.get_report_version(v1.id).content == res["content"]
    df_now = repo.load_measurements([sensor.id], START, END)
    assert data_fingerprint(df_now, [sensor.id], START, END) != v1.data_fingerprint

    # Neue Version → Freigabe zeigt sie
    _, v2 = _version(repo, room, sensor, report_id)
    res3 = sharing.resolve_shared_report(repo, token)
    assert res3["version"] == 2 and res3["content"]["quality"]["n"] > n1
    assert v2.data_fingerprint == data_fingerprint(df_now, [sensor.id], START, END)

    # Widerruf
    share = repo.list_shares(report_id)[0]
    repo.revoke_share(share.id)
    assert sharing.resolve_shared_report(repo, token) is None


def test_expired_and_invalid_tokens(env):
    repo, room, sensor = env
    report_id, _ = _version(repo, room, sensor)
    expired = _share(repo, report_id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert sharing.resolve_shared_report(repo, expired) is None
    valid = _share(repo, report_id, expires_at=sharing.expires_at_for(7))
    assert sharing.resolve_shared_report(repo, valid) is not None
    assert sharing.resolve_shared_report(repo, sharing.generate_token()) is None
    assert sharing.resolve_shared_report(repo, "kurz") is None
    assert sharing.resolve_shared_report(repo, "x" * 43 + "'; DROP TABLE shares;--") is None
    assert sharing.resolve_shared_report(repo, sharing.hash_token(valid)) is None  # Hash ist kein Token


def test_archived_or_deleted_report_not_shared(env):
    repo, room, sensor = env
    report_id, _ = _version(repo, room, sensor)
    token = _share(repo, report_id)
    repo.archive_report(report_id)
    assert sharing.resolve_shared_report(repo, token) is None
    report_id2, _ = _version(repo, room, sensor)
    token2 = _share(repo, report_id2)
    repo.delete_report(report_id2)
    assert sharing.resolve_shared_report(repo, token2) is None
