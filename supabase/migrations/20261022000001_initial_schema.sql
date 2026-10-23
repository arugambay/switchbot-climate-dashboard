-- =============================================================================
-- SwitchBot Climate Dashboard – Initiales Schema (Supabase / PostgreSQL 15+)
-- =============================================================================
-- Grundsätze:
--   * Jede Tabelle hat owner_id (uuid, Standard auth.uid(), Verweis auf auth.users,
--     ON DELETE CASCADE). Row Level Security (RLS) ist auf ALLEN Tabellen aktiv;
--     jede Policy erlaubt nur Zeilen mit owner_id = (select auth.uid()).
--     "(select auth.uid())" statt "auth.uid()": wird pro Abfrage nur einmal ausgewertet.
--   * Verweise zwischen Tabellen laufen über zusammengesetzte Fremdschlüssel
--     (id, owner_id). Dadurch kann ein Nutzer auf DB-Ebene niemals einen Sensor in den
--     Raum eines anderen Nutzers legen – zusätzlich prüfen die INSERT-Policies das.
--   * Freigabe-Tokens werden nur als SHA-256-Hash gespeichert (Spalte token_hash).
--     Öffentlicher Zugriff ausschließlich über public.get_shared_report(p_token).
--   * Die App verwendet NUR den anon-Key + JWT des angemeldeten Nutzers, nie service_role.
--
-- Ausführen: Supabase-Dashboard → SQL Editor → gesamte Datei einfügen → "Run".
-- =============================================================================

-- SHA-256 kommt aus dem eingebauten sha256() (PostgreSQL 11+); keine Extension nötig.

-- -----------------------------------------------------------------------------
-- Räume
-- -----------------------------------------------------------------------------
create table public.rooms (
    id           uuid primary key default gen_random_uuid(),
    owner_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name         text not null check (char_length(btrim(name)) between 1 and 100),
    -- Vergleichsform: getrimmt, Leerzeichen zusammengefasst, Kleinschreibung
    name_norm    text generated always as (lower(regexp_replace(btrim(name), '\s+', ' ', 'g'))) stored,
    description  text check (description is null or char_length(description) <= 2000),
    created_at   timestamptz not null default now(),
    archived_at  timestamptz,
    unique (id, owner_id)
);
comment on table public.rooms is 'Räume. Name frei änderbar, niemals Schlüssel.';
-- Aktive Räume eines Nutzers dürfen keinen gleichen (normalisierten) Namen haben.
create unique index rooms_active_name_uq on public.rooms (owner_id, name_norm) where archived_at is null;

-- -----------------------------------------------------------------------------
-- Sensoren
-- -----------------------------------------------------------------------------
create table public.sensors (
    id                   uuid primary key default gen_random_uuid(),
    owner_id             uuid not null default auth.uid() references auth.users (id) on delete cascade,
    room_id              uuid not null,
    name                 text not null check (char_length(btrim(name)) between 1 and 100),
    device_info          text check (device_info is null or char_length(device_info) <= 500),
    timezone             text not null default 'Europe/Berlin' check (char_length(timezone) <= 64),
    expected_interval_s  integer check (expected_interval_s is null or expected_interval_s > 0),
    created_at           timestamptz not null default now(),
    archived_at          timestamptz,
    unique (id, owner_id),
    foreign key (room_id, owner_id) references public.rooms (id, owner_id) on delete cascade
);
create index sensors_room_idx on public.sensors (room_id);
create index sensors_owner_idx on public.sensors (owner_id);

-- -----------------------------------------------------------------------------
-- Importprotokolle
-- -----------------------------------------------------------------------------
create table public.imports (
    id                   uuid primary key default gen_random_uuid(),
    owner_id             uuid not null default auth.uid() references auth.users (id) on delete cascade,
    sensor_id            uuid not null,
    room_id              uuid not null,
    file_name            text not null check (char_length(file_name) <= 500),
    file_sha256          text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
    status               text not null default 'pending'
                         check (status in ('pending', 'completed', 'failed', 'rolled_back')),
    rows_read            integer not null default 0 check (rows_read >= 0),
    rows_new             integer not null default 0 check (rows_new >= 0),
    rows_duplicate       integer not null default 0 check (rows_duplicate >= 0),
    rows_invalid         integer not null default 0 check (rows_invalid >= 0),
    rows_conflict        integer not null default 0 check (rows_conflict >= 0),
    ts_min               timestamptz,
    ts_max               timestamptz,
    expected_interval_s  integer check (expected_interval_s is null or expected_interval_s > 0),
    observed_interval_s  integer check (observed_interval_s is null or observed_interval_s >= 0),
    warnings             jsonb not null default '[]'::jsonb,
    details              jsonb not null default '{}'::jsonb,
    created_at           timestamptz not null default now(),
    finished_at          timestamptz,
    unique (id, owner_id),
    foreign key (sensor_id, owner_id) references public.sensors (id, owner_id) on delete cascade,
    foreign key (room_id, owner_id) references public.rooms (id, owner_id) on delete cascade
);
create index imports_sensor_hash_idx on public.imports (sensor_id, file_sha256);
create index imports_room_idx on public.imports (room_id);
create index imports_owner_idx on public.imports (owner_id);

-- -----------------------------------------------------------------------------
-- Messungen
-- -----------------------------------------------------------------------------
-- Primärschlüssel (sensor_id, ts) = UNIQUE(sensor_id, ts) und zugleich der Index für
-- Zeitbereichsabfragen pro Sensor. owner_id ist denormalisiert, damit die RLS-Prüfung
-- ohne Join auskommt. import_id ermöglicht den Rollback eines Imports.
create table public.measurements (
    sensor_id      uuid not null,
    owner_id       uuid not null default auth.uid() references auth.users (id) on delete cascade,
    ts             timestamptz not null,
    temperature    double precision check (temperature is null or temperature between -100 and 100),
    humidity       double precision check (humidity is null or humidity between 0 and 100),
    dew_point      double precision check (dew_point is null or dew_point between -100 and 100),
    vpd            double precision check (vpd is null or vpd >= 0),
    abs_humidity   double precision check (abs_humidity is null or abs_humidity >= 0),
    raw_timestamp  text check (raw_timestamp is null or char_length(raw_timestamp) <= 64),
    import_id      uuid,
    primary key (sensor_id, ts),
    foreign key (sensor_id, owner_id) references public.sensors (id, owner_id) on delete cascade,
    -- Wird ein Importprotokoll gelöscht, bleiben die Messungen erhalten (nur import_id → NULL).
    foreign key (import_id, owner_id) references public.imports (id, owner_id) on delete set null (import_id)
);
create index measurements_import_idx on public.measurements (import_id) where import_id is not null;
-- Kein eigener owner_id-Index: Abfragen filtern immer über (sensor_id, ts); spart Speicher.

-- -----------------------------------------------------------------------------
-- Messkonflikte (gleicher Sensor + Zeitpunkt, abweichende Werte)
-- -----------------------------------------------------------------------------
create table public.measurement_conflicts (
    id           uuid primary key default gen_random_uuid(),
    owner_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    sensor_id    uuid not null,
    import_id    uuid not null,
    ts           timestamptz not null,
    existing     jsonb not null,
    incoming     jsonb not null,
    resolution   text check (resolution is null or resolution in ('keep_existing', 'use_incoming')),
    resolved_at  timestamptz,
    created_at   timestamptz not null default now(),
    check ((resolution is null) = (resolved_at is null)),
    foreign key (sensor_id, owner_id) references public.sensors (id, owner_id) on delete cascade,
    foreign key (import_id, owner_id) references public.imports (id, owner_id) on delete cascade
);
create index conflicts_sensor_idx on public.measurement_conflicts (sensor_id, resolution);
create index conflicts_import_idx on public.measurement_conflicts (import_id);
create index conflicts_owner_idx on public.measurement_conflicts (owner_id);

-- -----------------------------------------------------------------------------
-- Berichte und unveränderliche Versionen
-- -----------------------------------------------------------------------------
create table public.reports (
    id                  uuid primary key default gen_random_uuid(),
    owner_id            uuid not null default auth.uid() references auth.users (id) on delete cascade,
    room_id             uuid not null,
    title               text not null check (char_length(btrim(title)) between 1 and 200),
    current_version_id  uuid,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    archived_at         timestamptz,
    unique (id, owner_id),
    foreign key (room_id, owner_id) references public.rooms (id, owner_id) on delete cascade
);
create index reports_room_idx on public.reports (room_id);
create index reports_owner_idx on public.reports (owner_id);

create table public.report_versions (
    id                uuid primary key default gen_random_uuid(),
    owner_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
    report_id         uuid not null,
    version           integer not null check (version >= 1),
    period_start      timestamptz not null,
    period_end        timestamptz not null,
    config            jsonb not null default '{}'::jsonb,
    content           jsonb not null check (pg_column_size(content) < 5000000),
    data_fingerprint  text not null check (char_length(data_fingerprint) <= 128),
    created_at        timestamptz not null default now(),
    check (period_end >= period_start),
    unique (report_id, version),
    unique (id, report_id),
    foreign key (report_id, owner_id) references public.reports (id, owner_id) on delete cascade
);
create index report_versions_owner_idx on public.report_versions (owner_id);

-- Die aktuelle Version muss zum selben Bericht gehören (zusammengesetzter Fremdschlüssel).
alter table public.reports
    add constraint reports_current_version_fk
    foreign key (current_version_id, id) references public.report_versions (id, report_id)
    on delete set null (current_version_id)
    deferrable initially deferred;

-- Versionsnummer pro Bericht fortlaufend vergeben (1, 2, 3 …). Der Bericht wird dabei
-- gesperrt, damit parallele Aufrufe keine doppelte Nummer erzeugen. Eine vom Client
-- mitgeschickte Nummer wird ignoriert.
create or replace function public.report_versions_assign_number()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    perform 1 from public.reports where id = new.report_id for update;
    -- Erstellungszeitpunkt serverseitig festlegen (nicht rückdatierbar).
    new.created_at := now();
    select coalesce(max(v.version), 0) + 1
      into new.version
      from public.report_versions v
     where v.report_id = new.report_id;
    return new;
end;
$$;

create trigger report_versions_assign_number
    before insert on public.report_versions
    for each row execute function public.report_versions_assign_number();

-- Versionen sind unveränderlich: jedes UPDATE wird abgelehnt.
create or replace function public.report_versions_forbid_update()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    raise exception 'report_versions sind unveränderlich (Version %)', old.version
        using errcode = 'P0001';
end;
$$;

create trigger report_versions_immutable
    before update on public.report_versions
    for each row execute function public.report_versions_forbid_update();

-- -----------------------------------------------------------------------------
-- Freigaben (nur Hash des Tokens!)
-- -----------------------------------------------------------------------------
create table public.shares (
    id                uuid primary key default gen_random_uuid(),
    owner_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
    report_id         uuid not null,
    token_hash        text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
    token_hint        text not null check (char_length(token_hint) <= 8),
    label             text check (label is null or char_length(label) <= 200),
    created_at        timestamptz not null default now(),
    expires_at        timestamptz,
    revoked_at        timestamptz,
    last_accessed_at  timestamptz,
    access_count      integer not null default 0 check (access_count >= 0),
    foreign key (report_id, owner_id) references public.reports (id, owner_id) on delete cascade
);
create index shares_report_idx on public.shares (report_id);
create index shares_owner_idx on public.shares (owner_id);

-- -----------------------------------------------------------------------------
-- Einstellungen (eine Zeile pro Nutzer)
-- -----------------------------------------------------------------------------
create table public.user_settings (
    owner_id    uuid primary key default auth.uid() references auth.users (id) on delete cascade,
    settings    jsonb not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

-- =============================================================================
-- Row Level Security
-- =============================================================================
alter table public.rooms                 enable row level security;
alter table public.sensors               enable row level security;
alter table public.imports               enable row level security;
alter table public.measurements          enable row level security;
alter table public.measurement_conflicts enable row level security;
alter table public.reports               enable row level security;
alter table public.report_versions       enable row level security;
alter table public.shares                enable row level security;
alter table public.user_settings         enable row level security;

-- --- rooms -------------------------------------------------------------------
create policy rooms_select on public.rooms for select to authenticated
    using (owner_id = (select auth.uid()));
create policy rooms_insert on public.rooms for insert to authenticated
    with check (owner_id = (select auth.uid()));
create policy rooms_update on public.rooms for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy rooms_delete on public.rooms for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- sensors -----------------------------------------------------------------
create policy sensors_select on public.sensors for select to authenticated
    using (owner_id = (select auth.uid()));
create policy sensors_insert on public.sensors for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.rooms r where r.id = room_id and r.owner_id = (select auth.uid()))
    );
create policy sensors_update on public.sensors for update to authenticated
    using (owner_id = (select auth.uid()))
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.rooms r where r.id = room_id and r.owner_id = (select auth.uid()))
    );
create policy sensors_delete on public.sensors for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- imports -----------------------------------------------------------------
create policy imports_select on public.imports for select to authenticated
    using (owner_id = (select auth.uid()));
create policy imports_insert on public.imports for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.sensors s where s.id = sensor_id and s.owner_id = (select auth.uid()))
        and exists (select 1 from public.rooms r where r.id = room_id and r.owner_id = (select auth.uid()))
    );
create policy imports_update on public.imports for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy imports_delete on public.imports for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- measurements ------------------------------------------------------------
create policy measurements_select on public.measurements for select to authenticated
    using (owner_id = (select auth.uid()));
create policy measurements_insert on public.measurements for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.sensors s where s.id = sensor_id and s.owner_id = (select auth.uid()))
    );
create policy measurements_update on public.measurements for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy measurements_delete on public.measurements for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- measurement_conflicts ---------------------------------------------------
create policy conflicts_select on public.measurement_conflicts for select to authenticated
    using (owner_id = (select auth.uid()));
create policy conflicts_insert on public.measurement_conflicts for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.sensors s where s.id = sensor_id and s.owner_id = (select auth.uid()))
        and exists (select 1 from public.imports i where i.id = import_id and i.owner_id = (select auth.uid()))
    );
create policy conflicts_update on public.measurement_conflicts for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy conflicts_delete on public.measurement_conflicts for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- reports -----------------------------------------------------------------
create policy reports_select on public.reports for select to authenticated
    using (owner_id = (select auth.uid()));
create policy reports_insert on public.reports for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.rooms r where r.id = room_id and r.owner_id = (select auth.uid()))
    );
create policy reports_update on public.reports for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy reports_delete on public.reports for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- report_versions (kein UPDATE: unveränderlich) ---------------------------
create policy report_versions_select on public.report_versions for select to authenticated
    using (owner_id = (select auth.uid()));
create policy report_versions_insert on public.report_versions for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.reports r where r.id = report_id and r.owner_id = (select auth.uid()))
    );
-- Kein DELETE für einzelne Versionen: Sonst könnte eine Versionsnummer nach dem Löschen mit
-- anderem Inhalt neu vergeben werden. Versionen verschwinden nur zusammen mit ihrem Bericht
-- (ON DELETE CASCADE, läuft mit den Rechten des Tabelleneigentümers).

-- --- shares ------------------------------------------------------------------
create policy shares_select on public.shares for select to authenticated
    using (owner_id = (select auth.uid()));
create policy shares_insert on public.shares for insert to authenticated
    with check (
        owner_id = (select auth.uid())
        and exists (select 1 from public.reports r where r.id = report_id and r.owner_id = (select auth.uid()))
    );
create policy shares_update on public.shares for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy shares_delete on public.shares for delete to authenticated
    using (owner_id = (select auth.uid()));

-- --- user_settings -----------------------------------------------------------
create policy user_settings_select on public.user_settings for select to authenticated
    using (owner_id = (select auth.uid()));
create policy user_settings_insert on public.user_settings for insert to authenticated
    with check (owner_id = (select auth.uid()));
create policy user_settings_update on public.user_settings for update to authenticated
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy user_settings_delete on public.user_settings for delete to authenticated
    using (owner_id = (select auth.uid()));

-- =============================================================================
-- Rechte: anon (nicht angemeldet) bekommt KEINEN Tabellenzugriff.
-- =============================================================================
revoke all on table
    public.rooms, public.sensors, public.imports, public.measurements,
    public.measurement_conflicts, public.reports, public.report_versions,
    public.shares, public.user_settings
from anon;

grant select, insert, update, delete on table
    public.rooms, public.sensors, public.imports, public.measurements,
    public.measurement_conflicts, public.reports, public.shares, public.user_settings
to authenticated;
-- Versionen: kein UPDATE-Recht (zusätzlich zum Trigger).
revoke update on table public.report_versions from authenticated;
revoke delete on table public.report_versions from authenticated;
grant select, insert on table public.report_versions to authenticated;

-- Härtung: Supabase-Standardrechte TRUNCATE (umgeht RLS), REFERENCES, TRIGGER entziehen.
revoke truncate, references, trigger on all tables in schema public from anon, authenticated;

-- Freigaben: Token-Hash, Bericht und Zähler sind nach dem Anlegen nicht änderbar.
revoke update on table public.shares from authenticated;
grant update (label, expires_at, revoked_at) on table public.shares to authenticated;

-- =============================================================================
-- Funktionen (RPC)
-- =============================================================================

-- Öffentlicher Abruf eines geteilten Berichts. Läuft mit den Rechten des Besitzers der
-- Funktion (SECURITY DEFINER), damit auch nicht angemeldete Besucher den Snapshot lesen
-- können – aber ausschließlich diesen: Rückgabe nur {content, version, created_at,
-- expires_at}, keine IDs, keine Rohdaten. Der Token wird hier gehasht, gespeichert ist
-- nur der Hash. search_path ist leer, alle Namen sind voll qualifiziert.
create or replace function public.get_shared_report(p_token text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_hash     text;
    v_share_id uuid;
    v_result   jsonb;
begin
    -- Formatprüfung vor jedem Tabellenzugriff (Länge + URL-sicherer Zeichensatz)
    if p_token is null
       or char_length(p_token) not between 32 and 128
       or p_token !~ '^[A-Za-z0-9_-]+$' then
        return null;
    end if;

    v_hash := encode(pg_catalog.sha256(convert_to(p_token, 'UTF8')), 'hex');

    select s.id,
           jsonb_build_object(
               'content',    v.content,
               'version',    v.version,
               'created_at', v.created_at,
               'expires_at', s.expires_at
           )
      into v_share_id, v_result
      from public.shares s
      join public.reports r
        on r.id = s.report_id
      join public.report_versions v
        on v.id = r.current_version_id and v.report_id = r.id
      join public.rooms ro
        on ro.id = r.room_id
     where s.token_hash = v_hash
       and s.revoked_at is null
       and (s.expires_at is null or s.expires_at > now())
       and r.archived_at is null
       and ro.archived_at is null;

    if v_share_id is null then
        return null;
    end if;

    update public.shares
       set access_count = access_count + 1,
           last_accessed_at = now()
     where id = v_share_id;

    return v_result;
end;
$$;

revoke all on function public.get_shared_report(text) from public;
grant execute on function public.get_shared_report(text) to anon, authenticated;

-- Erste/letzte Messung und Anzahl eines Sensors, ohne Daten zu übertragen.
-- SECURITY INVOKER: RLS des aufrufenden Nutzers gilt.
create or replace function public.measurement_extent(p_sensor uuid)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    select jsonb_build_object(
        'first_ts', min(m.ts),
        'last_ts',  max(m.ts),
        'count',    count(*)
    )
    from public.measurements m
    where m.sensor_id = p_sensor;
$$;

revoke all on function public.measurement_extent(uuid) from public, anon;
grant execute on function public.measurement_extent(uuid) to authenticated;

-- Atomarer Batch-Import: fügt alle Zeilen in EINER Transaktion ein, vorhandene
-- (sensor_id, ts) bleiben unverändert (ON CONFLICT DO NOTHING). Rückgabe: Anzahl
-- tatsächlich eingefügter Zeilen. SECURITY INVOKER → RLS und Insert-Policy greifen.
-- p_rows: [{"ts": "...", "temperature": 21.3, "humidity": 55, ..., "raw_timestamp": "..."}]
create or replace function public.import_measurements(p_import uuid, p_sensor uuid, p_rows jsonb)
returns integer
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_count integer;
begin
    if jsonb_typeof(p_rows) is distinct from 'array' then
        raise exception 'p_rows muss ein JSON-Array sein' using errcode = '22023';
    end if;

    with ins as (
        insert into public.measurements
            (sensor_id, ts, temperature, humidity, dew_point, vpd, abs_humidity, raw_timestamp, import_id)
        select p_sensor, x.ts, x.temperature, x.humidity, x.dew_point, x.vpd, x.abs_humidity,
               x.raw_timestamp, p_import
          from jsonb_to_recordset(p_rows) as x(
                   ts timestamptz,
                   temperature double precision,
                   humidity double precision,
                   dew_point double precision,
                   vpd double precision,
                   abs_humidity double precision,
                   raw_timestamp text)
        on conflict (sensor_id, ts) do nothing
        returning 1
    )
    select count(*) into v_count from ins;
    return v_count;
end;
$$;

revoke all on function public.import_measurements(uuid, uuid, jsonb) from public, anon;
grant execute on function public.import_measurements(uuid, uuid, jsonb) to authenticated;

-- Trigger-Funktionen sind nicht als RPC gedacht.
revoke all on function public.report_versions_assign_number() from public, anon, authenticated;
revoke all on function public.report_versions_forbid_update() from public, anon, authenticated;
