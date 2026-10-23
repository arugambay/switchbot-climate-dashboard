"""Seite „Berichte & Freigaben“.

Ablauf: Raum wählen → Zeitraum → Inhalte/Optionen → Bericht erstellen (Vorschau) → speichern
(Bericht + Version 1) → Freigabelink erzeugen. Unten: vorhandene Berichte mit Versionen,
Freigaben (Widerruf), Hinweis auf geänderte Datenbasis („Neue Version erstellen“), PDF,
Archivieren/Löschen.

Versionen sind unveränderlich. Freigabelinks zeigen immer die *aktuelle* Version eines
Berichts; diese ändert sich nur durch eine bewusste Aktion des Nutzers.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.domain.models import Report, ReportVersion, Room, Sensor, Share, utcnow
from climate_dashboard.reports import sharing
from climate_dashboard.reports.builder import SECTIONS, ReportOptions, build_snapshot, data_fingerprint
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import report_view, state
from climate_dashboard.ui.formatting import fmt_date_range, fmt_datetime

_DRAFT = "rep_draft"
_NEW_LINK = "rep_new_link"
_PREVIEW_VERSION = "rep_preview_version"
_PERIOD_MODES = ("Letzter Monat", "Letzte 3 Monate", "Gesamter Datenzeitraum", "Kalenderjahr", "Frei wählen")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _local_midnight(d: date, tz: str) -> datetime:
    return pd.Timestamp(d).tz_localize(tz).tz_convert("UTC").to_pydatetime()


def _extent(repo: Any, sensors: list[Sensor]) -> tuple[datetime | None, datetime | None, int]:
    first = last = None
    total = 0
    for s in sensors:
        a, b, n = repo.measurement_extent(s.id)
        total += n
        if a is not None:
            first = a if first is None else min(first, a)
        if b is not None:
            last = b if last is None else max(last, b)
    return first, last, total


def _expected_interval(sensors: list[Sensor]) -> int | None:
    values = {s.expected_interval_s for s in sensors if s.expected_interval_s}
    return values.pop() if len(values) == 1 else None


def _period_from_mode(mode: str, first: datetime, last: datetime, tz: str, key: str) -> tuple[datetime, datetime]:
    first_d = pd.Timestamp(first).tz_convert(tz).date()
    last_d = pd.Timestamp(last).tz_convert(tz).date()
    end_d = last_d + timedelta(days=1)
    if mode == "Letzter Monat":
        start_d = (pd.Timestamp(end_d) - pd.DateOffset(months=1)).date()
    elif mode == "Letzte 3 Monate":
        start_d = (pd.Timestamp(end_d) - pd.DateOffset(months=3)).date()
    elif mode == "Kalenderjahr":
        years = list(range(last_d.year, first_d.year - 1, -1))
        year = st.selectbox("Jahr", years, key=f"{key}_year")
        start_d, end_d = date(year, 1, 1), date(year + 1, 1, 1)
    elif mode == "Frei wählen":
        picked = st.date_input("Von – bis (einschließlich)", value=(max(first_d, last_d - timedelta(days=30)), last_d),
                               min_value=first_d, max_value=last_d, format="DD.MM.YYYY", key=f"{key}_range")
        if isinstance(picked, tuple | list) and len(picked) == 2:
            start_d, end_d = picked[0], picked[1] + timedelta(days=1)
        else:
            start_d = picked[0] if isinstance(picked, tuple | list) and picked else first_d
            end_d = start_d + timedelta(days=1)
    else:
        start_d = first_d
    start_d = max(start_d, first_d) if mode in ("Letzter Monat", "Letzte 3 Monate") else start_d
    return _local_midnight(start_d, tz), _local_midnight(end_d, tz)


def _share_status(share: Share, now: datetime) -> tuple[str, str]:
    if share.revoked_at is not None:
        return "widerrufen", "neutral"
    if share.expires_at is not None and share.expires_at <= now:
        return "abgelaufen", "neutral"
    return "aktiv", "ok"


def _make_snapshot(repo: Any, room: Room, sensors: list[Sensor], start: datetime, end: datetime,
                   options: ReportOptions, version: int | None) -> tuple[dict[str, Any], str, pd.DataFrame]:
    sensor_ids = [s.id for s in sensors]
    df = state.load_measurements(sensor_ids, start, end) if sensor_ids else None
    snap = build_snapshot(df, room=room, sensors=sensors, period_start=start, period_end=end, options=options,
                          settings=state.analysis_settings(), expected_s=_expected_interval(sensors),
                          version=version)
    return snap, data_fingerprint(df, sensor_ids, start, end), df


def _config(options: ReportOptions, sensors: list[Sensor], start: datetime, end: datetime, mode: str) -> dict:
    return {"options": options.to_dict(), "sensor_ids": [s.id for s in sensors], "period_mode": mode,
            "period_start": start.isoformat(), "period_end": end.isoformat()}


def _pdf_download(snapshot: dict[str, Any], key: str, label: str = "PDF herunterladen") -> None:
    snapshot_json = report_view.snapshot_key(snapshot)
    name = f"Klimabericht-v{(snapshot.get('meta') or {}).get('version') or 'entwurf'}.pdf"
    st.download_button(label, data=lambda: _pdf_cached(snapshot_json), file_name=name, mime="application/pdf",
                       icon=":material/picture_as_pdf:", key=key, on_click="ignore")


@st.cache_data(show_spinner=False, max_entries=8, ttl=3600)
def _pdf_cached(snapshot_json: str) -> bytes:
    from climate_dashboard.reports.pdf import render_pdf

    return render_pdf(json.loads(snapshot_json))


def _link_hints(app_settings: Any) -> None:
    if app_settings.is_local:
        ui.callout("hinweis", "Lokaler Modus",
                   "Freigabelinks funktionieren nur auf diesem Rechner, solange die App hier läuft.")
    if not app_settings.app_base_url:
        ui.callout("info", "Keine Basis-URL konfiguriert",
                   "APP_BASE_URL ist nicht gesetzt – der Link wird relativ angezeigt (/?r=…). Stellen Sie beim "
                   "Weitergeben die Adresse der App voran.")


# ---------------------------------------------------------------------------
# Neuer Bericht
# ---------------------------------------------------------------------------
def _new_report(repo: Any, rooms: list[Room]) -> None:
    ui.section_header("Neuer Bericht", "Raum und Zeitraum wählen, Inhalte festlegen, Vorschau prüfen, speichern.")
    tz = state.analysis_settings().timezone
    default_idx = 0
    sel = state.selected_room_id()
    ids = [r.id for r in rooms]
    if sel in ids:
        default_idx = ids.index(sel)
    room = st.selectbox("1 · Raum", rooms, index=default_idx, format_func=lambda r: r.name, key="rep_room")
    sensors = repo.list_sensors(room.id, include_archived=True)
    first, last, total = _extent(repo, sensors)
    if not sensors or total == 0 or first is None or last is None:
        ui.empty_state("Keine Messdaten in diesem Raum",
                       "Importieren Sie zuerst eine CSV-Datei (Seite „Import“), um einen Bericht zu erstellen.")
        return
    st.caption(f"Daten vorhanden: {fmt_date_range(first, last, tz)} · {total:,} Messungen".replace(",", "."))

    mode = st.radio("2 · Zeitraum", _PERIOD_MODES, horizontal=True, index=2, key="rep_mode")
    start, end = _period_from_mode(mode, first, last, tz, "rep")
    if end <= start:
        ui.callout("hinweis", "Ungültiger Zeitraum", "Das Ende muss nach dem Beginn liegen.")
        return
    st.caption(f"Berichtszeitraum: {fmt_date_range(start, end, tz, end_exclusive=True)}")

    st.markdown("**3 · Inhalte und Optionen**")
    cols = st.columns(3)
    chosen = []
    for i, (sid, label) in enumerate(SECTIONS.items()):
        with cols[i % 3]:
            if st.checkbox(label, value=True, key=f"rep_sec_{sid}"):
                chosen.append(sid)
    title = st.text_input("Titel", value=f"Klimabericht {room.name}", max_chars=120, key=f"rep_title_{room.id}")
    note = st.text_area("Notiz für die Empfänger (optional)", max_chars=1000, key="rep_note",
                        placeholder="z. B. Anlass der Messung, Position des Sensors im Raum …")
    show_sensor = st.checkbox("Sensorname und Geräteinfo im Bericht anzeigen", value=False, key="rep_sensor",
                              help="Standardmäßig nicht enthalten – nur aktivieren, wenn die Angaben geteilt "
                                   "werden dürfen.")
    use_surface = st.checkbox("Oberflächentemperatur annehmen (optional)", value=False, key="rep_surf",
                              help="Ohne Oberflächenmessung kann eine Annahme getroffen werden, z. B. dass eine "
                                   "Außenwandecke etwa 4 K kälter ist als die Raumluft. Alle Ergebnisse daraus "
                                   "werden im Bericht als ANNAHME gekennzeichnet.")
    offset = None
    if use_surface:
        offset = st.number_input("Angenommene Differenz Raumluft → kalte Oberfläche (K)", min_value=0.5,
                                 max_value=15.0, value=4.0, step=0.5, key="rep_offset")
    options = ReportOptions(title=title, include_sections=tuple(chosen), show_sensor_info=show_sensor,
                            surface_offset_k=offset, note=note)

    if st.button("4 · Bericht erstellen (Vorschau)", type="primary", key="rep_build"):
        with ui.loading("Bericht wird berechnet …"):
            snap, fp, _ = _make_snapshot(repo, room, sensors, start, end, options, version=1)
        st.session_state[_DRAFT] = {"room_id": room.id, "snapshot": snap, "fingerprint": fp,
                                    "config": _config(options, sensors, start, end, mode),
                                    "start": start, "end": end, "title": options.title}

    draft = st.session_state.get(_DRAFT)
    if not draft or draft.get("room_id") != room.id:
        return
    ui.section_header("Vorschau", "So sehen Empfänger des Freigabelinks den Bericht.")
    with st.container(border=True):
        report_view.render_report(draft["snapshot"], key="preview")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("5 · Bericht speichern", type="primary", key="rep_save"):
            rep = repo.create_report(room.id, draft["title"])
            repo.add_report_version(rep.id, period_start=draft["start"], period_end=draft["end"],
                                    config=draft["config"], content=draft["snapshot"],
                                    data_fingerprint=draft["fingerprint"], make_current=True)
            st.session_state.pop(_DRAFT, None)
            st.session_state["rep_open"] = rep.id
            st.toast("Bericht gespeichert (Version 1). Unten können Sie jetzt einen Freigabelink erzeugen.")
            st.rerun()
    with c2:
        if st.button("Entwurf verwerfen", key="rep_discard"):
            st.session_state.pop(_DRAFT, None)
            st.rerun()


# ---------------------------------------------------------------------------
# Vorhandene Berichte
# ---------------------------------------------------------------------------
def _new_version(repo: Any, report: Report, current: ReportVersion, room: Room) -> None:
    cfg = current.config or {}
    options = ReportOptions.from_dict(cfg.get("options"))
    all_sensors = {s.id: s for s in repo.list_sensors(report.room_id, include_archived=True)}
    sensors = [all_sensors[i] for i in cfg.get("sensor_ids") or [] if i in all_sensors] or list(all_sensors.values())
    number = max(v.version for v in repo.list_report_versions(report.id)) + 1
    snap, fp, _ = _make_snapshot(repo, room, sensors, current.period_start, current.period_end, options, number)
    repo.add_report_version(report.id, period_start=current.period_start, period_end=current.period_end,
                            config=cfg, content=snap, data_fingerprint=fp, make_current=True)


def _shares_block(repo: Any, report: Report, app_settings: Any) -> None:
    now = utcnow()
    shares = repo.list_shares(report.id)
    st.markdown("**Freigabelinks**")
    new_link = st.session_state.get(_NEW_LINK)
    if new_link and new_link.get("report_id") == report.id:
        ui.callout("hinweis", "Neuer Freigabelink – jetzt kopieren",
                   "Jeder, der diesen Link hat, kann den Bericht sehen und weitergeben. Der Link wird nur jetzt "
                   "angezeigt und lässt sich später nicht erneut abrufen (gespeichert wird nur ein Prüfwert). "
                   "Bei Bedarf können Sie den Link jederzeit widerrufen.")
        st.code(new_link["url"], language=None)
        if st.button("Link kopiert – ausblenden", key=f"rep_hide_{report.id}"):
            st.session_state.pop(_NEW_LINK, None)
            st.rerun()
    if shares:
        rows = []
        for s in shares:
            status, _lvl = _share_status(s, now)
            rows.append([s.label or "–", f"…{s.token_hint}", fmt_datetime(s.created_at),
                         fmt_datetime(s.expires_at) if s.expires_at else "kein Ablauf", str(s.access_count),
                         fmt_datetime(s.last_accessed_at) if s.last_accessed_at else "–", status])
        st.html(report_view.table_html(["Bezeichnung", "Link endet auf", "Erstellt", "Läuft ab", "Zugriffe",
                                        "Letzter Zugriff", "Status"], rows, numeric={4}))
        active = [s for s in shares if _share_status(s, now)[0] == "aktiv"]
        if active:
            c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
            with c1:
                target = st.selectbox("Freigabe widerrufen", active, key=f"rep_rev_sel_{report.id}",
                                      format_func=lambda s: f"{s.label or 'ohne Bezeichnung'} (…{s.token_hint})")
            with c2:
                sure = st.checkbox("Ja, dieser Link soll sofort nicht mehr funktionieren",
                                   key=f"rep_rev_ok_{report.id}")
            with c3:
                if st.button("Widerrufen", disabled=not sure, key=f"rep_rev_{report.id}"):
                    repo.revoke_share(target.id)
                    st.toast("Freigabe widerrufen.")
                    st.rerun()
    else:
        st.caption("Noch keine Freigaben.")

    with st.form(f"rep_share_form_{report.id}", border=True):
        st.markdown("Neuen Freigabelink erzeugen")
        c1, c2 = st.columns(2)
        with c1:
            label = st.text_input("Bezeichnung (nur für Sie sichtbar)", placeholder="z. B. Hausverwaltung Müller",
                                  max_chars=80)
        with c2:
            labels = list(sharing.EXPIRY_OPTIONS)
            expiry = st.selectbox("Gültigkeit", labels, index=labels.index(sharing.DEFAULT_EXPIRY_LABEL))
        if st.form_submit_button("6 · Freigabelink erzeugen", type="primary"):
            token = sharing.generate_token()
            repo.create_share(report.id, token_hash=sharing.hash_token(token), token_hint=sharing.token_hint(token),
                              label=label.strip() or None,
                              expires_at=sharing.expires_at_for(sharing.EXPIRY_OPTIONS[expiry]))
            st.session_state[_NEW_LINK] = {"report_id": report.id,
                                           "url": sharing.share_url(app_settings.app_base_url, token)}
            st.session_state["rep_open"] = report.id
            st.rerun()
    _link_hints(app_settings)


def _report_block(repo: Any, report: Report, rooms_by_id: dict[str, Room], app_settings: Any) -> None:
    room = rooms_by_id.get(report.room_id)
    versions = repo.list_report_versions(report.id)
    current = next((v for v in versions if v.id == report.current_version_id), versions[-1] if versions else None)
    tz = state.analysis_settings().timezone
    archived = report.archived_at is not None
    head = f"{report.title} · {room.name if room else 'Raum gelöscht'}"
    if current:
        head += f" · Version {current.version} · {fmt_date_range(current.period_start, current.period_end, tz, end_exclusive=True)}"
    if archived:
        head += " · archiviert"
    with st.expander(head, expanded=st.session_state.get("rep_open") == report.id):
        if current is None:
            ui.callout("hinweis", "Dieser Bericht hat keine Version.")
            return
        st.caption(f"Aktuelle Version {current.version} vom {fmt_datetime(current.created_at)} · "
                   f"{len(versions)} Version(en) insgesamt · Freigabelinks zeigen immer die aktuelle Version.")

        # --- Datenbasis geändert? ------------------------------------------------------------
        if room and not archived:
            sensor_ids = (current.config or {}).get("sensor_ids") or []
            df = state.load_measurements(sensor_ids, current.period_start, current.period_end) if sensor_ids else None
            fp_now = data_fingerprint(df, sensor_ids, current.period_start, current.period_end)
            if fp_now != current.data_fingerprint:
                ui.callout("hinweis", "Datenbasis geändert",
                           "Seit dieser Version wurden Messungen im Berichtszeitraum ergänzt oder geändert. Die "
                           "gespeicherte Version bleibt unverändert. Mit „Neue Version erstellen“ wird der Bericht "
                           "mit gleichem Raum, Zeitraum und gleichen Optionen neu berechnet; die neue Version wird "
                           "aktuell und alle bestehenden Freigabelinks zeigen dann die neue Version.")
                if st.button("Neue Version erstellen", type="primary", key=f"rep_nv_{report.id}"):
                    with ui.loading("Neue Version wird berechnet …"):
                        _new_version(repo, report, current, room)
                    st.session_state["rep_open"] = report.id
                    st.toast("Neue Version erstellt – Freigaben zeigen jetzt diese Version.")
                    st.rerun()
            else:
                st.caption("Datenbasis unverändert seit der aktuellen Version.")

        # --- Versionen ------------------------------------------------------------------------
        st.markdown("**Versionen**")
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        with c1:
            chosen = st.selectbox(
                "Version", list(reversed(versions)), key=f"rep_ver_{report.id}",
                format_func=lambda v: f"Version {v.version} vom {fmt_datetime(v.created_at)}"
                + (" (aktuell)" if v.id == report.current_version_id else ""),
            )
        with c2:
            showing = st.session_state.get(_PREVIEW_VERSION) == chosen.id
            if st.button("Ausblenden" if showing else "Ansehen", key=f"rep_view_{report.id}"):
                st.session_state[_PREVIEW_VERSION] = None if showing else chosen.id
                st.rerun()
        with c3:
            _pdf_download(chosen.content, key=f"rep_pdf_{report.id}_{chosen.id}")
        if chosen.id != report.current_version_id and not archived:
            if st.button(f"Version {chosen.version} als aktuelle Version festlegen", key=f"rep_cur_{report.id}",
                         help="Freigabelinks zeigen danach diese Version."):
                repo.set_current_version(report.id, chosen.id)
                st.rerun()
        if st.session_state.get(_PREVIEW_VERSION) == chosen.id:
            with st.container(border=True):
                report_view.render_report(chosen.content, key=f"v{chosen.version}_{report.id[:8]}")

        # --- Freigaben ------------------------------------------------------------------------
        if not archived:
            _shares_block(repo, report, app_settings)

        # --- Archivieren / Löschen ----------------------------------------------------------
        st.markdown("**Bericht verwalten**")
        if not archived:
            st.caption("Archivieren: Der Bericht bleibt mit allen Versionen erhalten, ist aber über "
                       "Freigabelinks nicht mehr erreichbar.")
            ok_a = st.checkbox("Archivieren bestätigen", key=f"rep_arch_ok_{report.id}")
            if st.button("Archivieren", disabled=not ok_a, key=f"rep_arch_{report.id}"):
                repo.archive_report(report.id)
                st.rerun()
        st.caption("Löschen: Entfernt den Bericht mit allen Versionen und Freigaben endgültig; alle Links "
                   "funktionieren danach nicht mehr.")
        ok_d = st.checkbox("Ja, endgültig löschen", key=f"rep_del_ok_{report.id}")
        if st.button("Löschen", disabled=not ok_d, key=f"rep_del_{report.id}"):
            repo.delete_report(report.id)
            st.toast("Bericht gelöscht.")
            st.rerun()


def _existing_reports(repo: Any, rooms: list[Room], app_settings: Any) -> None:
    ui.section_header("Vorhandene Berichte", "Versionen, Freigabelinks und Datenstand")
    show_archived = st.toggle("Archivierte anzeigen", value=False, key="rep_show_arch")
    reports = repo.list_reports(include_archived=show_archived)
    if not reports:
        st.caption("Noch keine gespeicherten Berichte.")
        return
    rooms_by_id = {r.id: r for r in repo.list_rooms(include_archived=True)}
    for rep in reports:
        _report_block(repo, rep, rooms_by_id, app_settings)


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------
def render() -> None:
    ui.page_header("Berichte & Freigaben",
                   "Schreibgeschützte Klimaberichte erstellen, versionieren und per Link teilen.",
                   eyebrow="Auswertung")
    repo = state.repo()
    app_settings = state.settings()
    rooms = repo.list_rooms()
    if not rooms:
        ui.empty_state("Noch keine Räume", "Legen Sie über den Import einen Raum an und laden Sie Messdaten hoch – "
                       "danach können Sie hier Berichte erstellen.")
        return
    tab_new, tab_list = st.tabs(["Neuer Bericht", "Vorhandene Berichte"])
    with tab_new:
        _new_report(repo, rooms)
    with tab_list:
        _existing_reports(repo, rooms, app_settings)


__all__ = ["render"]
