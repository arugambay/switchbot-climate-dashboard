"""Statische Prüfungen der Supabase-Migrationen.

Es gibt keinen Postgres in der Testumgebung. Die Isolation fremder Nutzer (RLS) kann daher
nur statisch geprüft werden: Jede Tabelle hat RLS aktiviert, Policies für alle
Operationen, die nur owner_id = (select auth.uid()) erlauben, keine Klartext-Token-Spalte,
die öffentliche Funktion ist SECURITY DEFINER mit leerem search_path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


@pytest.fixture(scope="module")
def sql() -> str:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, "keine Migrationen gefunden"
    for f in files:
        assert re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", f.name), f.name
    return _strip_comments("\n".join(f.read_text(encoding="utf-8") for f in files)).lower()


@pytest.fixture(scope="module")
def tables(sql) -> list[str]:
    return re.findall(r"create table (?:if not exists )?public\.(\w+)", sql)


def _table_body(sql: str, table: str) -> str:
    m = re.search(rf"create table (?:if not exists )?public\.{table} \((.*?)\n\);", sql, re.S)
    assert m, table
    return m.group(1)


def _policies(sql: str, table: str) -> list[tuple[str, str]]:
    """(operation, policy text) aller Policies einer Tabelle."""
    out = []
    for m in re.finditer(rf"create policy \w+ on public\.{table}\s+for (\w+)(.*?);", sql, re.S):
        out.append((m.group(1), m.group(2)))
    return out


def test_expected_tables(tables):
    assert set(tables) == {
        "rooms",
        "sensors",
        "imports",
        "measurements",
        "measurement_conflicts",
        "reports",
        "report_versions",
        "shares",
        "user_settings",
    }


def test_every_table_has_rls_enabled(sql, tables):
    for t in tables:
        assert re.search(rf"alter table public\.{t}\s+enable row level security", sql), t


def test_every_table_has_owner_id_referencing_auth_users(sql, tables):
    for t in tables:
        body = _table_body(sql, t)
        assert re.search(
            r"owner_id\s+uuid not null default auth\.uid\(\) references auth\.users \(id\) on delete cascade",
            body,
        ) or re.search(r"owner_id\s+uuid primary key default auth\.uid\(\) references auth\.users", body), t


def test_policies_cover_all_operations_and_check_owner(sql, tables):
    for t in tables:
        pols = _policies(sql, t)
        ops = {op for op, _ in pols}
        # report_versions: unveränderlich und nicht einzeln löschbar (nur per Kaskade mit dem Bericht)
        expected = {"select", "insert"} | (set() if t == "report_versions" else {"update", "delete"})
        assert expected <= ops, (t, ops)
        assert "all" not in ops
        for op, text in pols:
            assert "to authenticated" in text, (t, op)
            assert "owner_id = (select auth.uid())" in text, (t, op)
            if op in ("select", "delete"):
                assert "using" in text
            if op == "insert":
                assert "with check" in text
            if op == "update":
                assert "using" in text and "with check" in text
            assert " anon" not in text
            assert "true)" not in text.replace(" ", "")


def test_insert_policies_check_referenced_ownership(sql):
    expectations = {
        "sensors": ["public.rooms"],
        "imports": ["public.sensors", "public.rooms"],
        "measurements": ["public.sensors"],
        "measurement_conflicts": ["public.sensors", "public.imports"],
        "reports": ["public.rooms"],
        "report_versions": ["public.reports"],
        "shares": ["public.reports"],
    }
    for table, refs in expectations.items():
        inserts = [text for op, text in _policies(sql, table) if op == "insert"]
        assert len(inserts) == 1, table
        for ref in refs:
            assert re.search(
                rf"exists \(select 1 from {re.escape(ref)} \w+ where .*owner_id = \(select auth\.uid\(\)\)",
                inserts[0],
            ), (table, ref)


def test_composite_foreign_keys_bind_owner(sql):
    for table, ref in [
        ("sensors", "rooms"),
        ("imports", "sensors"),
        ("measurements", "sensors"),
        ("reports", "rooms"),
        ("report_versions", "reports"),
        ("shares", "reports"),
    ]:
        assert re.search(rf"references public\.{ref} \(id, owner_id\)", _table_body(sql, table)), table


def test_measurements_unique_and_checks(sql):
    body = _table_body(sql, "measurements")
    assert "primary key (sensor_id, ts)" in body
    assert re.search(r"\bts\s+timestamptz not null", body)
    assert "humidity between 0 and 100" in body
    assert "temperature between -100 and 100" in body
    assert "import_id" in body and "raw_timestamp" in body
    assert "measurements_import_idx" in sql


def test_room_name_unique_among_active(sql):
    assert re.search(
        r"create unique index rooms_active_name_uq on public\.rooms \(owner_id, name_norm\)\s+"
        r"where archived_at is null",
        sql,
    )


def test_status_and_resolution_enums(sql):
    assert "status in ('pending', 'completed', 'failed', 'rolled_back')" in _table_body(sql, "imports")
    assert "resolution in ('keep_existing', 'use_incoming')" in _table_body(sql, "measurement_conflicts")


def test_report_versions_immutable(sql):
    assert "unique (report_id, version)" in _table_body(sql, "report_versions")
    assert re.search(
        r"create trigger report_versions_immutable\s+before update on public\.report_versions", sql
    )
    assert re.search(r"revoke update on table public\.report_versions from authenticated", sql)
    assert not [op for op, _ in _policies(sql, "report_versions") if op == "update"]


def test_no_plaintext_token_column(sql, tables):
    for t in tables:
        body = _table_body(sql, t)
        cols = re.findall(r"^\s+(\w+)\s+(?:uuid|text|integer|jsonb|timestamptz|double precision)", body, re.M)
        for c in cols:
            assert not (("token" in c) and c not in ("token_hash", "token_hint")), (t, c)
    shares = _table_body(sql, "shares")
    assert re.search(
        r"token_hash\s+text not null unique check \(token_hash ~ '\^\[0-9a-f\]\{64\}\$'\)", shares
    )


def _function(sql: str, name: str) -> str:
    m = re.search(rf"create or replace function public\.{name}\((.*?)\$\$;", sql, re.S)
    assert m, name
    return m.group(0)


def test_get_shared_report_is_safe_security_definer(sql):
    fn = _function(sql, "get_shared_report")
    assert "p_token text" in fn and "returns jsonb" in fn
    assert "security definer" in fn
    assert "set search_path = ''" in fn
    assert "pg_catalog.sha256(" in fn and "'hex'" in fn
    assert "revoked_at is null" in fn
    assert "expires_at is null or s.expires_at > now()" in fn
    assert "r.archived_at is null" in fn
    assert "access_count = access_count + 1" in fn and "last_accessed_at = now()" in fn
    # Rückgabe ausschließlich diese vier Schlüssel
    build = re.search(r"jsonb_build_object\((.*?)\)\s+into", fn, re.S).group(1)
    keys = re.findall(r"'(\w+)',", build)
    assert keys == ["content", "version", "created_at", "expires_at"]
    # alle Tabellen voll qualifiziert
    for ref in re.findall(r"\b(?:from|join|update)\s+([\w.]+)", fn):
        assert ref.startswith("public.") or ref == "ins", ref
    assert re.search(r"revoke all on function public\.get_shared_report\(text\) from public;", sql)
    assert re.search(
        r"grant execute on function public\.get_shared_report\(text\) to anon, authenticated;", sql
    )


def test_other_functions_invoker_with_search_path(sql):
    for name in (
        "measurement_extent",
        "import_measurements",
        "report_versions_assign_number",
        "report_versions_forbid_update",
    ):
        fn = _function(sql, name)
        assert "security invoker" in fn, name
        assert "set search_path = ''" in fn, name
    imp = _function(sql, "import_measurements")
    assert "on conflict (sensor_id, ts) do nothing" in imp
    assert re.search(r"revoke all on function public\.measurement_extent\(uuid\) from public, anon;", sql)
    assert re.search(
        r"revoke all on function public\.import_measurements\(uuid, uuid, jsonb\) from public, anon;", sql
    )


def test_only_one_security_definer(sql):
    assert sql.count("security definer") == 1


def test_anon_has_no_table_access(sql, tables):
    m = re.search(r"revoke all on table(.*?)from anon;", sql, re.S)
    assert m
    for t in tables:
        assert f"public.{t}" in m.group(1), t
    assert not re.search(r"grant [^;]* on table [^;]* to [^;]*\banon\b", sql)


def test_shares_token_hash_not_updatable(sql):
    assert "revoke update on table public.shares from authenticated" in sql
    assert "grant update (label, expires_at, revoked_at) on table public.shares to authenticated" in sql


def test_no_service_role_usage(sql):
    assert "service_role" not in sql


def test_report_versions_cannot_be_deleted_or_backdated(sql):
    assert "report_versions_delete" not in sql
    assert "revoke delete on table public.report_versions from authenticated" in sql
    assert "new.created_at := now();" in sql


def test_shared_report_requires_active_room_and_builtin_hash(sql):
    assert "ro.archived_at is null" in sql
    assert "create extension" not in sql and "pg_catalog.sha256(" in sql
