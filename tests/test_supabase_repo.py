"""Tests für SupabaseRepository und SupabaseAuth mit einem Fake-Client.

Es gibt keine echte Supabase-Verbindung in den Tests. Der Fake bildet die verwendete
Teilmenge der supabase-py/postgrest-API nach (table/select/insert/update/delete/upsert,
Filter, order, range, rpc, execute) inkl. PostgREST-Limit "max rows".
RLS/Policies selbst werden in test_migrations_sql.py statisch geprüft.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pandas as pd
import pytest
from postgrest.exceptions import APIError

from climate_dashboard.auth.session import AuthError, AuthSession, LocalAuth, SupabaseAuth
from climate_dashboard.domain.models import (
    HUMIDITY,
    METRICS,
    SENSOR_ID,
    TEMPERATURE,
    TS,
    ConflictResolution,
    MeasurementConflict,
)
from climate_dashboard.reports.sharing import generate_token, hash_token, resolve_shared_report
from climate_dashboard.storage.base import IntegrityError, NotFoundError, Repository, RepositoryError
from climate_dashboard.storage.supabase_repo import (
    ConnectionFailedError,
    PermissionDeniedError,
    SessionExpiredError,
    SupabaseRepository,
    translate_error,
)
from tests.synthetic import make_measurements


# ---------------------------------------------------------------------------
# Fake supabase client
# ---------------------------------------------------------------------------
def _parse_ts(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _cmp_value(col, v):
    if col in ("ts", "created_at", "updated_at") and v is not None:
        return _parse_ts(v)
    return v


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op, self.payload, self.filters, self.orders = "select", None, [], []
        self.rng = None
        self.lim = None
        self.kwargs: dict[str, Any] = {}

    # Operationen
    def select(self, cols="*", **kw):
        self.op, self.cols = "select", cols
        return self

    def insert(self, payload, **kw):
        self.op, self.payload, self.kwargs = "insert", payload, kw
        return self

    def upsert(self, payload, **kw):
        self.op, self.payload, self.kwargs = "upsert", payload, kw
        return self

    def update(self, payload, **kw):
        self.op, self.payload, self.kwargs = "update", payload, kw
        return self

    def delete(self, **kw):
        self.op, self.kwargs = "delete", kw
        return self

    # Filter
    def eq(self, c, v):
        self.filters.append((c, "eq", v))
        return self

    def gte(self, c, v):
        self.filters.append((c, "gte", v))
        return self

    def gt(self, c, v):
        self.filters.append((c, "gt", v))
        return self

    def lt(self, c, v):
        self.filters.append((c, "lt", v))
        return self

    def is_(self, c, v):
        self.filters.append((c, "is", v))
        return self

    def order(self, c, desc=False):
        self.orders.append((c, desc))
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def _match(self, row):
        for c, op, v in self.filters:
            x = row.get(c)
            if op == "is":
                if (x is None) != (v == "null"):
                    return False
                continue
            a, b = _cmp_value(c, x), _cmp_value(c, v)
            if op == "eq" and a != b:
                return False
            if op == "gte" and not a >= b:
                return False
            if op == "gt" and not a > b:
                return False
            if op == "lt" and not a < b:
                return False
        return True

    def execute(self):
        self.client.log.append(self)
        if self.client.fail_next:
            exc, self.client.fail_next = self.client.fail_next, None
            raise exc
        rows = self.client.tables.setdefault(self.table, [])
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for item in items:
                row = {"id": str(uuid.uuid4()), "created_at": datetime.now(UTC).isoformat(), **item}
                hook = self.client.insert_hooks.get(self.table)
                if hook:
                    hook(row, rows)
                rows.append(row)
                out.append(dict(row))
            return SimpleNamespace(data=out, count=None)
        if self.op == "upsert":
            key = self.kwargs["on_conflict"]
            existing = [r for r in rows if r.get(key) == self.payload[key]]
            if existing:
                existing[0].update(self.payload)
            else:
                rows.append(dict(self.payload))
            return SimpleNamespace(data=[], count=None)
        matched = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in matched], count=None)
        if self.op == "delete":
            self.client.tables[self.table] = [r for r in rows if r not in matched]
            data = [] if self.kwargs.get("returning") == "minimal" else [dict(r) for r in matched]
            return SimpleNamespace(data=data, count=len(matched) if self.kwargs.get("count") else None)
        for c, desc in reversed(self.orders):
            matched.sort(
                key=lambda r, c=c: (_cmp_value(c, r.get(c)) is None, _cmp_value(c, r.get(c))), reverse=desc
            )
        if self.rng:
            matched = matched[self.rng[0] : self.rng[1] + 1]
        matched = matched[: self.client.max_rows]
        if self.lim is not None:
            matched = matched[: self.lim]
        if self.cols != "*":
            keep = self.cols.split(",")
            matched = [{k: r.get(k) for k in keep} for r in matched]
        return SimpleNamespace(data=[dict(r) for r in matched], count=None)


class FakeRpc:
    def __init__(self, client, fn, params):
        self.client, self.fn, self.params = client, fn, params

    def execute(self):
        self.client.log.append(self)
        if self.client.fail_next:
            exc, self.client.fail_next = self.client.fail_next, None
            raise exc
        return SimpleNamespace(data=self.client.rpc_handlers[self.fn](self.params))


class FakeClient:
    def __init__(self, max_rows=1000):
        self.tables: dict[str, list[dict]] = {}
        self.log: list = []
        self.max_rows = max_rows
        self.fail_next: Exception | None = None
        self.insert_hooks = {"report_versions": self._version_hook}
        self.rpc_handlers = {
            "import_measurements": self._rpc_import,
            "measurement_extent": self._rpc_extent,
            "get_shared_report": self._rpc_shared,
        }

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params=None):
        return FakeRpc(self, fn, params or {})

    # Nachbildung der DB-Logik aus der Migration
    @staticmethod
    def _version_hook(row, rows):
        row["version"] = 1 + max(
            (r["version"] for r in rows if r["report_id"] == row["report_id"]), default=0
        )

    def _rpc_import(self, p):
        rows = self.tables.setdefault("measurements", [])
        keys = {(r["sensor_id"], _parse_ts(r["ts"])) for r in rows}
        n = 0
        for item in p["p_rows"]:
            k = (p["p_sensor"], _parse_ts(item["ts"]))
            if k in keys:
                continue
            keys.add(k)
            rows.append({**item, "sensor_id": p["p_sensor"], "import_id": p["p_import"]})
            n += 1
        return n

    def _rpc_extent(self, p):
        ts = [
            _parse_ts(r["ts"]) for r in self.tables.get("measurements", []) if r["sensor_id"] == p["p_sensor"]
        ]
        if not ts:
            return {"first_ts": None, "last_ts": None, "count": 0}
        return {"first_ts": min(ts).isoformat(), "last_ts": max(ts).isoformat(), "count": len(ts)}

    def _rpc_shared(self, p):
        h = hash_token(p["p_token"])
        for s in self.tables.get("shares", []):
            if s["token_hash"] == h and s.get("revoked_at") is None:
                rep = next(r for r in self.tables["reports"] if r["id"] == s["report_id"])
                v = next(v for v in self.tables["report_versions"] if v["id"] == rep["current_version_id"])
                # absichtlich ein zusätzliches Feld, das der Client herausfiltern muss
                return {
                    "content": v["content"],
                    "version": v["version"],
                    "created_at": v["created_at"],
                    "expires_at": s.get("expires_at"),
                    "report_id": rep["id"],
                }
        return None


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def repo(client):
    return SupabaseRepository(client, user_id="user-1")


def _measure_payload_rows(client):
    return [q for q in client.log if isinstance(q, FakeRpc) and q.fn == "import_measurements"]


# ---------------------------------------------------------------------------
# Tests Repository
# ---------------------------------------------------------------------------
def test_implements_protocol(repo):
    assert isinstance(repo, Repository)


def test_room_sensor_crud_without_owner_id(repo, client):
    room = repo.create_room("  Wohn   zimmer ", "Süd")
    assert room.name == "Wohn zimmer"
    sensor = repo.create_sensor(room.id, "S1", expected_interval_s=600)
    assert repo.get_sensor(sensor.id).expected_interval_s == 600
    renamed = repo.rename_room(room.id, "Wohnzimmer")
    assert renamed.id == room.id
    # owner_id wird nie vom Client gesendet (DB-Default auth.uid())
    for q in client.log:
        if isinstance(q, FakeQuery) and q.payload is not None and q.table != "user_settings":
            items = q.payload if isinstance(q.payload, list) else [q.payload]
            assert all("owner_id" not in i for i in items)
    with pytest.raises(NotFoundError):
        repo.get_room("missing")
    with pytest.raises(RepositoryError):
        repo.update_sensor(sensor.id, owner_id="x")


def test_insert_in_batches_counts_and_dedups(client):
    repo = SupabaseRepository(client, "u", insert_batch_size=100)
    df = make_measurements(end="2026-03-06 00:00")  # 240 Zeilen
    assert repo.insert_measurements("s1", "imp1", df) == 240
    calls = _measure_payload_rows(client)
    assert [len(c.params["p_rows"]) for c in calls] == [100, 100, 40]
    first = calls[0].params["p_rows"][0]
    assert set(first) == {"ts", *METRICS, "raw_timestamp"}
    assert first["ts"].endswith("Z")
    assert calls[0].params["p_import"] == "imp1" and calls[0].params["p_sensor"] == "s1"
    assert repo.insert_measurements("s1", "imp2", df) == 0


def test_nan_sent_as_null(repo, client):
    df = make_measurements(end="2026-03-01 01:00")
    df.loc[0, HUMIDITY] = float("nan")
    repo.insert_measurements("s1", "i", df)
    assert _measure_payload_rows(client)[0].params["p_rows"][0][HUMIDITY] is None


def test_load_paginates_with_keyset_and_range(client):
    client.max_rows = 100
    repo = SupabaseRepository(client, "u", page_size=100)
    df = make_measurements(end="2026-03-06 00:00", tz="UTC")  # 240
    repo.insert_measurements("s1", "i", df)
    client.log.clear()
    out = repo.load_measurements(["s1"])
    selects = [q for q in client.log if isinstance(q, FakeQuery) and q.table == "measurements"]
    assert len(selects) == 3  # 100 + 100 + 40
    assert all(q.rng == (0, 99) for q in selects)
    assert ("ts", "gt") in [(c, op) for c, op, _ in selects[1].filters]
    assert len(out) == 240 and out[TS].is_monotonic_increasing and not out[TS].duplicated().any()
    assert list(out.columns) == [SENSOR_ID, TS, *METRICS]
    assert str(out[TS].dtype) == "datetime64[ns, UTC]"
    assert out[TEMPERATURE].tolist() == df[TEMPERATURE].tolist()


def test_load_half_open_range_and_multi_sensor(repo):
    repo.insert_measurements("s1", "i", make_measurements(end="2026-03-02 00:00", tz="UTC"))
    repo.insert_measurements("s2", "i", make_measurements(end="2026-03-02 00:00", tz="UTC", seed=3))
    start = datetime(2026, 3, 1, 1, tzinfo=UTC)
    out = repo.load_measurements(["s1", "s2"], start, start + timedelta(hours=1))
    assert len(out) == 4
    assert out[TS].min() == pd.Timestamp(start) and out[TS].max() == pd.Timestamp(
        start + timedelta(minutes=30)
    )
    assert repo.load_measurements([]).empty
    assert repo.load_measurements(["none"]).empty


def test_extent_via_rpc(repo, client):
    assert repo.measurement_extent("s1") == (None, None, 0)
    repo.insert_measurements("s1", "i", make_measurements(end="2026-03-02 00:00", tz="UTC"))
    first, last, n = repo.measurement_extent("s1")
    assert (first, last, n) == (
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 3, 1, 23, 30, tzinfo=UTC),
        48,
    )


def test_delete_of_import_uses_count_minimal(repo, client):
    repo.insert_measurements("s1", "imp1", make_measurements(end="2026-03-01 05:00"))
    assert repo.delete_measurements_of_import("imp1") == 10
    q = client.log[-1]
    assert q.op == "delete" and q.kwargs == {"count": "exact", "returning": "minimal"}


def test_conflict_resolution(repo, client):
    repo.insert_measurements("s1", "i", make_measurements(end="2026-03-01 01:00", tz="UTC"))
    ts = datetime(2026, 3, 1, 0, 30, tzinfo=UTC)
    c = MeasurementConflict(
        id="c1",
        sensor_id="s1",
        import_id="i",
        ts=ts,
        existing={TEMPERATURE: 20.0},
        incoming={TEMPERATURE: 30.0},
    )
    repo.add_conflicts([c])
    res = repo.resolve_conflict("c1", ConflictResolution.USE_INCOMING)
    assert res.resolution is ConflictResolution.USE_INCOMING and res.existing == {TEMPERATURE: 20.0}
    assert repo.load_measurements(["s1"], ts, ts + timedelta(seconds=1))[TEMPERATURE].iloc[0] == 30.0
    with pytest.raises(IntegrityError):
        repo.resolve_conflict("c1", ConflictResolution.KEEP_EXISTING)


def test_delete_conflicts_of_import(repo, client):
    ts = datetime(2026, 3, 1, 0, 30, tzinfo=UTC)
    conflicts = [
        MeasurementConflict(id=f"c{i}", sensor_id="s1", import_id=imp, ts=ts + timedelta(minutes=i),
                            existing={TEMPERATURE: 20.0}, incoming={TEMPERATURE: 21.0})
        for i, imp in enumerate(["imp1", "imp1", "imp2"])
    ]
    repo.add_conflicts(conflicts)
    assert repo.delete_conflicts_of_import("imp1") == 2
    q = client.log[-1]
    assert q.table == "measurement_conflicts" and q.op == "delete"
    assert q.kwargs == {"count": "exact", "returning": "minimal"}
    assert [c.id for c in repo.list_conflicts("s1")] == ["c2"]


def test_raw_timestamp_is_sent(repo, client):
    df = make_measurements(end="2026-03-01 01:00", tz="UTC")
    df["raw_timestamp"] = ["a", "b"]
    repo.insert_measurements("s1", "imp1", df)
    got = repo.load_measurements(["s1"], include_raw=True)
    assert got["raw_timestamp"].tolist() == ["a", "b"]


def test_report_versions_and_current(repo):
    room = repo.create_room("R")
    rep = repo.create_report(room.id, "Bericht")
    kw = dict(
        period_start=datetime(2026, 3, 1, tzinfo=UTC),
        period_end=datetime(2026, 4, 1, tzinfo=UTC),
        config={},
        data_fingerprint="fp",
    )
    v1 = repo.add_report_version(rep.id, content={"n": 1}, **kw)
    v2 = repo.add_report_version(rep.id, content={"n": 2}, make_current=False, **kw)
    assert (v1.version, v2.version) == (1, 2)
    assert repo.get_report(rep.id).current_version_id == v1.id
    assert repo.set_current_version(rep.id, v2.id).current_version_id == v2.id
    with pytest.raises(NotFoundError):
        repo.set_current_version(rep.id, "fremd")


def test_share_flow_and_resolve_token(repo, client):
    room = repo.create_room("R")
    rep = repo.create_report(room.id, "B")
    repo.add_report_version(
        rep.id,
        period_start=datetime(2026, 3, 1, tzinfo=UTC),
        period_end=datetime(2026, 4, 1, tzinfo=UTC),
        config={},
        content={"x": 1},
        data_fingerprint="fp",
    )
    token = generate_token()
    with pytest.raises(IntegrityError):
        repo.create_share(rep.id, token_hash=token, token_hint=token[-4:])
    share = repo.create_share(rep.id, token_hash=hash_token(token), token_hint=token[-4:])
    stored = client.tables["shares"][0]
    assert token not in repr(stored)  # Klartext wird nie an die DB geschickt

    public = SupabaseRepository(client, user_id=None)
    result = resolve_shared_report(public, token)
    assert result == {
        "content": {"x": 1},
        "version": 1,
        "created_at": result["created_at"],
        "expires_at": None,
    }
    rpc = client.log[-1]
    assert rpc.fn == "get_shared_report" and rpc.params == {"p_token": token}
    assert public.resolve_share_token("garbage") is None
    assert client.log[-1] is rpc  # unplausibler Token → keine Anfrage
    with pytest.raises(RepositoryError):
        public.resolve_share(hash_token(token))
    repo.revoke_share(share.id)
    assert public.resolve_share_token(token) is None
    assert repo.revoke_share(share.id).revoked_at is not None  # idempotent


def test_settings_roundtrip(repo, client):
    assert repo.get_settings() == {}
    repo.save_settings({"humidity_high": 70.0})
    assert repo.get_settings() == {"humidity_high": 70.0}
    q = client.log[-2]
    assert q.op == "upsert" and q.kwargs["on_conflict"] == "owner_id" and q.payload["owner_id"] == "user-1"
    with pytest.raises(SessionExpiredError):
        SupabaseRepository(client, None).save_settings({})


# ---------------------------------------------------------------------------
# Fehlerübersetzung
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "expected", "fragment"),
    [
        (APIError({"code": "PGRST301", "message": "JWT expired"}), SessionExpiredError, "abgelaufen"),
        (
            APIError({"code": "42501", "message": "new row violates row-level security policy"}),
            PermissionDeniedError,
            "Berechtigung",
        ),
        (
            APIError(
                {
                    "code": "23505",
                    "message": 'duplicate key value violates unique constraint "rooms_active_name_uq"',
                }
            ),
            IntegrityError,
            "aktiven Raum",
        ),
        (APIError({"code": "23503", "message": "fk"}), IntegrityError, "Verknüpftes Objekt"),
        (APIError({"code": "23514", "message": "check"}), IntegrityError, "Ungültiger Wert"),
        (
            APIError({"code": "P0001", "message": "report_versions sind unveränderlich"}),
            IntegrityError,
            "unveränderlich",
        ),
        (
            APIError({"code": "22P02", "message": "invalid input syntax for type uuid"}),
            NotFoundError,
            "nicht gefunden",
        ),
        (APIError({"code": "XX000", "message": "boom"}), RepositoryError, "Datenbankfehler"),
    ],
)
def test_api_error_translation(error, expected, fragment):
    out = translate_error(error)
    assert type(out) is expected
    assert fragment in str(out)


def _http_status_error(status):
    req = httpx.Request("GET", "https://x.supabase.co/rest/v1/rooms")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


def test_http_and_network_error_translation():
    assert isinstance(translate_error(_http_status_error(401)), SessionExpiredError)
    assert isinstance(translate_error(_http_status_error(403)), PermissionDeniedError)
    assert isinstance(translate_error(_http_status_error(503)), ConnectionFailedError)
    net = translate_error(httpx.ConnectError("no route"))
    assert isinstance(net, ConnectionFailedError) and "pausieren" in str(net)
    assert isinstance(translate_error(httpx.ReadTimeout("t")), ConnectionFailedError)


def test_errors_are_translated_in_repo_calls(repo, client):
    client.fail_next = APIError({"code": "42501", "message": "permission denied for table rooms"})
    with pytest.raises(PermissionDeniedError):
        repo.list_rooms()
    client.fail_next = httpx.ConnectError("down")
    with pytest.raises(ConnectionFailedError):
        repo.measurement_extent("s1")


# ---------------------------------------------------------------------------
# Kein Service-Key
# ---------------------------------------------------------------------------
def test_no_service_key_in_storage_code():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "climate_dashboard"
    for name in ("storage/supabase_repo.py", "storage/factory.py", "auth/session.py"):
        text = (root / name).read_text(encoding="utf-8").lower()
        assert "service_role_key" not in text
        assert "settings.supabase_service" not in text
    import climate_dashboard.config as cfg

    assert not hasattr(cfg.Settings, "supabase_service_role_key")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class FakeAuthApi:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple] = []

    def _resp(self):
        user = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", email="a@b.de")
        session = SimpleNamespace(
            access_token="acc", refresh_token="ref", expires_at=2_000_000_000, user=user
        )
        return SimpleNamespace(user=user, session=session)

    def sign_in_with_password(self, creds):
        self.calls.append(("sign_in", creds["email"]))
        if self.fail:
            raise self.fail
        return self._resp()

    def refresh_session(self, token):
        self.calls.append(("refresh", token))
        if self.fail:
            raise self.fail
        return self._resp()

    def set_session(self, a, r):
        self.calls.append(("set_session", a, r))

    def sign_out(self):
        self.calls.append(("sign_out",))


def test_supabase_auth_sign_in_and_refresh():
    api = FakeAuthApi()
    auth = SupabaseAuth(SimpleNamespace(auth=api))
    s = auth.sign_in(" a@b.de ", "geheim")
    assert s.user_id.startswith("1111") and s.email == "a@b.de"
    assert s.access_token == "acc" and s.expires_at == datetime.fromtimestamp(2_000_000_000, UTC)
    assert "acc" not in repr(s) and "ref" not in repr(s)
    assert api.calls == [("sign_in", "a@b.de")]
    expiring = AuthSession(
        "u", access_token="old", refresh_token="ref", expires_at=datetime.now(UTC) + timedelta(seconds=10)
    )
    assert expiring.is_expired()
    assert auth.ensure_fresh(expiring).access_token == "acc"
    auth.sign_out(s)
    assert api.calls[-2:] == [("set_session", "acc", "ref"), ("sign_out",)]


def test_supabase_auth_errors_do_not_leak_password(caplog):
    api = FakeAuthApi(fail=Exception("Invalid login credentials"))
    auth = SupabaseAuth(SimpleNamespace(auth=api))
    with pytest.raises(AuthError) as ei:
        auth.sign_in("a@b.de", "SuperGeheim123")
    assert "falsch" in str(ei.value)
    assert "SuperGeheim123" not in str(ei.value) and "SuperGeheim123" not in caplog.text
    assert ei.value.__cause__ is None
    with pytest.raises(AuthError):
        auth.sign_in("", "x")
    api.fail = Exception("Invalid Refresh Token: Already Used")
    with pytest.raises(AuthError, match="abgelaufen"):
        auth.refresh(AuthSession("u", refresh_token="r"))


def test_local_auth():
    s = LocalAuth().current_session()
    assert s.user_id == "local" and s.is_local and not LocalAuth.requires_login
    assert not s.is_expired()
