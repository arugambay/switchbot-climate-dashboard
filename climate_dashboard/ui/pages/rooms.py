"""Seite „Räume & Sensoren“ – Verwaltung, Importprotokolle und Konfliktauflösung.

Grundsätze:
* IDs bleiben bei Umbenennungen erhalten; Namen sind frei änderbar.
* Ähnliche Raumnamen werden angezeigt, Räume aber nie automatisch zusammengeführt.
* Löschen nur nach Anzeige der Auswirkungen und Eintippen des Raumnamens.
* Für Übersichten wird ``measurement_extent`` genutzt – es werden keine Messreihen geladen.
* Nach jeder Änderung ``state.invalidate_data()``.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st

from climate_dashboard.domain.models import (
    METRIC_LABELS,
    METRICS,
    ConflictResolution,
    ImportStatus,
    Room,
    Sensor,
)
from climate_dashboard.domain.rooms import find_similar_rooms, is_duplicate_name, validate_room_name
from climate_dashboard.storage.base import RepositoryError
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import selectors as sel
from climate_dashboard.ui import state
from climate_dashboard.ui.formatting import (
    fmt_datetime,
    fmt_duration,
    fmt_metric,
    fmt_number,
    fmt_percent,
)

FLASH_KEY = "rooms_flash"
PENDING_KEY = "rooms_pending_create"
INTERVALS: dict[int | None, str] = {
    None: "automatisch erkennen",
    60: "1 Min.",
    120: "2 Min.",
    300: "5 Min.",
    600: "10 Min.",
    900: "15 Min.",
    1800: "30 Min.",
    3600: "60 Min.",
}
TIMEZONES = ("Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "UTC")
STATUS_LABELS = {
    ImportStatus.PENDING: "läuft/abgebrochen",
    ImportStatus.COMPLETED: "abgeschlossen",
    ImportStatus.FAILED: "fehlgeschlagen",
    ImportStatus.ROLLED_BACK: "zurückgerollt",
}
CONFLICT_PAGE = 25


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _done(message: str) -> None:
    """Nach einer Änderung: Cache leeren, Meldung merken, neu laden."""
    state.invalidate_data()
    st.session_state[FLASH_KEY] = message
    st.rerun()


def _flash() -> None:
    msg = st.session_state.pop(FLASH_KEY, None)
    if msg:
        ui.callout("ok", msg)


def _try(action, message: str) -> None:
    try:
        action()
    except RepositoryError as exc:
        ui.callout("auffaellig", "Änderung nicht möglich", str(exc))
        return
    _done(message)


def _valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _interval_label(value: int | None) -> str:
    return INTERVALS.get(value) or fmt_duration(value)


def _completeness(first: Any, last: Any, n: int, expected_s: int | None) -> float | None:
    """Messvollständigkeit aus Anzahl und Zeitraum (ohne Daten zu laden)."""
    if not n or not expected_s or first is None or last is None:
        return None
    span = (last - first).total_seconds() + expected_s
    return min(100.0, n / (span / expected_s) * 100.0) if span > 0 else None


def _sensor_rows(sensors: list[Sensor], tz: str) -> list[dict[str, Any]]:
    rows = []
    for s in sensors:
        first, last, n = sel.sensor_extent(s.id)
        rows.append({
            "Sensor": s.name,
            "Status": "archiviert" if s.is_archived else "aktiv",
            "Messzeitraum": sel.period_label(first, last, tz) if n else "–",
            "Messungen": fmt_number(n),
            "Vollständigkeit": fmt_percent(_completeness(first, last, n, s.expected_interval_s)),
            "Intervall": _interval_label(s.expected_interval_s),
            "Zeitzone": s.timezone,
            "Gerät": s.device_info or "–",
        })  # fmt: skip
    return rows


# ---------------------------------------------------------------------------
# Raum anlegen (mit Ähnlichkeitswarnung)
# ---------------------------------------------------------------------------
def _create_room(all_rooms: list[Room]) -> None:
    repo = state.repo()
    with st.expander("Neuen Raum anlegen", expanded=PENDING_KEY in st.session_state):
        with st.form("room_create", clear_on_submit=False, border=False):
            name = st.text_input("Name des Raums", max_chars=80, key="room_new_name")
            desc = st.text_input("Beschreibung (optional)", key="room_new_desc")
            submitted = st.form_submit_button("Raum anlegen", type="primary")
        if submitted:
            error = validate_room_name(name)
            if error:
                ui.callout("hinweis", error)
            elif is_duplicate_name(name, [r for r in all_rooms if not r.is_archived]):
                ui.callout("hinweis", "Name bereits vergeben", "Es gibt bereits einen aktiven Raum mit diesem Namen.")
            else:
                similar = find_similar_rooms(name, all_rooms)
                if similar:
                    st.session_state[PENDING_KEY] = {"name": name.strip(), "description": desc.strip() or None}
                else:
                    _try(lambda: repo.create_room(name.strip(), desc.strip() or None), f"Raum „{name.strip()}“ angelegt.")
        pending = st.session_state.get(PENDING_KEY)
        if pending:
            similar = find_similar_rooms(pending["name"], all_rooms)
            names = ", ".join(
                f"„{r.name}“" + (" (archiviert)" if r.is_archived else "") for r, _score in similar
            )
            ui.callout(
                "hinweis",
                "Ähnlicher Raum vorhanden",
                f"Es gibt bereits {names}. Meinst du einen vorhandenen Raum? Räume werden nicht automatisch "
                "zusammengeführt.",
            )
            cols = st.columns(len(similar[:3]) + 2)
            for i, (room, _score) in enumerate(similar[:3]):
                if cols[i].button(f"„{room.name}“ verwenden", key=f"room_use_{room.id}"):
                    st.session_state.pop(PENDING_KEY, None)
                    state.set_selected_room_id(room.id)
                    st.session_state[FLASH_KEY] = f"Vorhandener Raum „{room.name}“ ausgewählt."
                    st.rerun()
            if cols[-2].button("Trotzdem neu anlegen", key="room_create_anyway"):
                st.session_state.pop(PENDING_KEY, None)
                _try(lambda: repo.create_room(pending["name"], pending["description"]),
                     f"Raum „{pending['name']}“ angelegt.")  # fmt: skip
            if cols[-1].button("Abbrechen", key="room_create_cancel"):
                st.session_state.pop(PENDING_KEY, None)
                st.rerun()


# ---------------------------------------------------------------------------
# Raum bearbeiten
# ---------------------------------------------------------------------------
def _tab_room(room: Room, all_rooms: list[Room]) -> None:
    repo = state.repo()
    with st.form(f"room_rename_{room.id}", border=False):
        new_name = st.text_input("Name", value=room.name, max_chars=80, key=f"room_name_{room.id}")
        st.caption(
            "Die interne ID bleibt erhalten; Messungen, Sensoren und Berichte bleiben zugeordnet. Bereits erstellte "
            "Berichtsversionen (Snapshots) zeigen den alten Namen, bis eine neue Version erstellt wird."
        )
        rename = st.form_submit_button("Umbenennen")
    if rename and new_name.strip() != room.name:
        error = validate_room_name(new_name)
        others = [r for r in all_rooms if r.id != room.id]
        if error:
            ui.callout("hinweis", error)
        else:
            similar = [r for r, _ in find_similar_rooms(new_name, others)]
            if similar:
                ui.callout("info", "Ähnliche Namen vorhanden", ", ".join(f"„{r.name}“" for r in similar))
            _try(lambda: repo.rename_room(room.id, new_name.strip()), f"Raum in „{new_name.strip()}“ umbenannt.")

    with st.form(f"room_desc_{room.id}", border=False):
        desc = st.text_area("Beschreibung", value=room.description or "", key=f"room_desc_text_{room.id}", height=80)
        save_desc = st.form_submit_button("Beschreibung speichern")
    if save_desc:
        _try(lambda: repo.update_room(room.id, description=desc.strip() or None), "Beschreibung gespeichert.")

    if room.is_archived:
        st.caption(f"Archiviert am {fmt_datetime(room.archived_at)}. Archivierte Räume erscheinen nicht in Übersicht und Analyse.")
        if st.button("Reaktivieren", key=f"room_unarchive_{room.id}"):
            _try(lambda: repo.unarchive_room(room.id), f"Raum „{room.name}“ reaktiviert.")
    else:
        st.caption("Archivieren blendet den Raum aus Übersicht und Analyse aus; Daten bleiben erhalten.")
        if st.button("Archivieren", key=f"room_archive_{room.id}"):
            _try(lambda: repo.archive_room(room.id), f"Raum „{room.name}“ archiviert.")

    _delete_room(room)


def _delete_room(room: Room) -> None:
    repo = state.repo()
    with st.expander("Raum löschen"):
        sensors = repo.list_sensors(room.id, include_archived=True)
        measurements = sum(sel.sensor_extent(s.id)[2] for s in sensors)
        reports = repo.list_reports(room.id, include_archived=True)
        active_shares = sum(1 for r in reports for sh in repo.list_shares(r.id) if sh.is_active())
        imports = repo.list_imports(room_id=room.id)
        ui.callout(
            "auffaellig",
            "Das Löschen kann nicht rückgängig gemacht werden",
            f"Gelöscht werden: {len(sensors)} Sensor(en), {fmt_number(measurements)} Messungen, "
            f"{len(imports)} Importprotokoll(e), {len(reports)} Bericht(e) mit allen Versionen. "
            f"{active_shares} aktive Freigabelink(s) funktionieren danach nicht mehr.",
        )
        typed = st.text_input(f"Zum Bestätigen den Raumnamen „{room.name}“ eintippen", key=f"room_del_confirm_{room.id}")
        ok = typed.strip() == room.name
        if st.button("Raum endgültig löschen", type="primary", disabled=not ok, key=f"room_delete_{room.id}"):

            def delete() -> None:
                repo.delete_room(room.id)
                if state.selected_room_id() == room.id:
                    state.set_selected_room_id(None)

            _try(delete, f"Raum „{room.name}“ gelöscht.")


# ---------------------------------------------------------------------------
# Sensoren
# ---------------------------------------------------------------------------
def _sensor_fields(prefix: str, sensor: Sensor | None = None) -> dict[str, Any]:
    name = st.text_input("Name", value=sensor.name if sensor else "", max_chars=80, key=f"{prefix}_name")
    device = st.text_input("Gerät (Modell, Seriennummer …)", value=(sensor.device_info or "") if sensor else "",
                           key=f"{prefix}_device")  # fmt: skip
    current_tz = sensor.timezone if sensor else "Europe/Berlin"
    options = list(dict.fromkeys([current_tz, *TIMEZONES]))
    tz = st.selectbox("Zeitzone der Zeitstempel in den CSV-Dateien", options, key=f"{prefix}_tz")
    current_iv = sensor.expected_interval_s if sensor else None
    iv_options = list(dict.fromkeys([current_iv, *INTERVALS]))
    interval = st.selectbox("Erwartetes Messintervall", iv_options, format_func=_interval_label, key=f"{prefix}_iv")
    return {"name": name.strip(), "device_info": device.strip() or None, "timezone": tz, "expected_interval_s": interval}


def _tab_sensors(room: Room, rooms: list[Room], tz: str) -> None:
    repo = state.repo()
    show_archived = st.toggle("Archivierte Sensoren anzeigen", key=f"sens_arch_{room.id}")
    sensors = repo.list_sensors(room.id, include_archived=show_archived)
    if sensors:
        sel.table(pd.DataFrame(_sensor_rows(sensors, tz)), key=f"sens_table_{room.id}")
        st.caption("Vollständigkeit = Anzahl Messungen im Verhältnis zur erwarteten Anzahl zwischen erster und "
                   "letzter Messung (ohne Laden der Messreihen).")  # fmt: skip
    else:
        ui.callout("info", "Keine Sensoren", "Sensoren werden beim Import automatisch angelegt oder hier manuell.")

    with st.expander("Sensor anlegen"):
        with st.form(f"sens_create_{room.id}", border=False):
            fields = _sensor_fields(f"sens_new_{room.id}")
            create = st.form_submit_button("Sensor anlegen", type="primary")
        if create:
            if not fields["name"]:
                ui.callout("hinweis", "Bitte einen Namen für den Sensor eingeben.")
            else:
                _try(lambda: repo.create_sensor(room.id, fields["name"], device_info=fields["device_info"],
                                                timezone=fields["timezone"],
                                                expected_interval_s=fields["expected_interval_s"]),
                     f"Sensor „{fields['name']}“ angelegt.")  # fmt: skip

    if not sensors:
        return
    by_id = {s.id: s for s in sensors}
    sid = st.selectbox("Sensor bearbeiten", list(by_id), format_func=lambda i: by_id[i].name, key=f"sens_pick_{room.id}")
    sensor = by_id[sid]
    with st.form(f"sens_edit_{sensor.id}", border=False):
        fields = _sensor_fields(f"sens_edit_{sensor.id}", sensor)
        st.caption("Zeitzone und Intervall wirken auf künftige Importe und Auswertungen; gespeicherte Messzeitpunkte "
                   "(UTC) werden nicht verändert.")  # fmt: skip
        save = st.form_submit_button("Änderungen speichern")
    if save:
        if not fields["name"]:
            ui.callout("hinweis", "Der Name darf nicht leer sein.")
        elif not _valid_tz(fields["timezone"]):
            ui.callout("hinweis", "Unbekannte Zeitzone.")
        else:
            _try(lambda: repo.update_sensor(sensor.id, **fields), f"Sensor „{fields['name']}“ gespeichert.")

    others = [r for r in rooms if r.id != room.id]
    with st.expander("Anderem Raum zuordnen"):
        if not others:
            st.caption("Es gibt keinen anderen Raum.")
        else:
            target = st.selectbox("Ziel-Raum", [r.id for r in others], key=f"sens_move_to_{sensor.id}",
                                  format_func=lambda i: next(r.name for r in others if r.id == i))  # fmt: skip
            ui.callout(
                "hinweis",
                "Auswirkungen",
                f"Alle Messungen von „{sensor.name}“ gehören danach zum Ziel-Raum. Analysen und neue Berichte von "
                f"„{room.name}“ enthalten diesen Sensor nicht mehr; bestehende Berichtsversionen bleiben unverändert.",
            )
            confirm = st.checkbox("Ich habe die Auswirkungen verstanden", key=f"sens_move_ok_{sensor.id}")
            if st.button("Sensor verschieben", disabled=not confirm, key=f"sens_move_{sensor.id}"):
                _try(lambda: repo.update_sensor(sensor.id, room_id=target), f"Sensor „{sensor.name}“ verschoben.")

    if sensor.is_archived:
        if st.button("Sensor reaktivieren", key=f"sens_unarch_{sensor.id}"):
            _try(lambda: repo.unarchive_sensor(sensor.id), f"Sensor „{sensor.name}“ reaktiviert.")
    elif st.button("Sensor archivieren", key=f"sens_archive_{sensor.id}"):
        _try(lambda: repo.archive_sensor(sensor.id), f"Sensor „{sensor.name}“ archiviert.")


# ---------------------------------------------------------------------------
# Importprotokolle
# ---------------------------------------------------------------------------
def _tab_imports(room: Room, tz: str) -> None:
    repo = state.repo()
    sensors = {s.id: s.name for s in repo.list_sensors(room.id, include_archived=True)}
    imports = repo.list_imports(room_id=room.id)
    if not imports:
        ui.callout("info", "Keine Importe", "Für diesen Raum wurde noch nichts importiert.")
        return
    rows = [
        {
            "Datum": fmt_datetime(r.created_at, tz),
            "Datei": r.file_name,
            "Sensor": sensors.get(r.sensor_id, "–"),
            "Status": STATUS_LABELS.get(r.status, str(r.status)),
            "Gelesen": fmt_number(r.rows_read),
            "Neu": fmt_number(r.rows_new),
            "Bereits vorhanden": fmt_number(r.rows_duplicate),
            "Ungültig": fmt_number(r.rows_invalid),
            "Konflikte": fmt_number(r.rows_conflict),
            "Zeitraum": sel.period_label(r.ts_min, r.ts_max, tz) if r.ts_min else "–",
            "Warnungen": len(r.warnings),
        }
        for r in imports
    ]
    sel.table(pd.DataFrame(rows), key=f"imports_{room.id}")
    with_warnings = [r for r in imports if r.warnings]
    for r in with_warnings[:10]:
        with st.expander(f"Warnungen: {r.file_name} ({fmt_datetime(r.created_at, tz)})"):
            for w in r.warnings:
                st.caption(f"• {w}")


# ---------------------------------------------------------------------------
# Konflikte
# ---------------------------------------------------------------------------
def _conflict_table(existing: dict, incoming: dict) -> pd.DataFrame:
    rows = []
    for m in METRICS:
        if m not in existing and m not in incoming:
            continue
        a, b = existing.get(m), incoming.get(m)
        rows.append({
            "Messgröße": METRIC_LABELS[m], "Vorhandener Wert": fmt_metric(a, m), "Neuer Wert": fmt_metric(b, m),
            "Unterschied": "" if a == b else "≠",
        })  # fmt: skip
    return pd.DataFrame(rows)


def _tab_conflicts(room: Room, tz: str) -> None:
    repo = state.repo()
    sensors = repo.list_sensors(room.id, include_archived=True)
    names = {s.id: s.name for s in sensors}
    conflicts = [c for s in sensors for c in repo.list_conflicts(s.id)]
    if not conflicts:
        ui.callout("ok", "Keine offenen Konflikte", "Es gibt keine abweichenden Werte für bereits vorhandene Zeitpunkte.")
        return
    st.caption(
        "Ein Konflikt entsteht, wenn ein Import für einen bereits gespeicherten Zeitpunkt andere Werte enthält. "
        "Es wird nichts automatisch überschrieben. Bei „Neuen Wert übernehmen“ bleibt der alte Wert im "
        "Konfliktprotokoll nachvollziehbar."
    )
    for c in conflicts[:CONFLICT_PAGE]:
        with st.container(border=True):
            st.markdown(f"**{fmt_datetime(c.ts, tz, seconds=True)}** · Sensor „{names.get(c.sensor_id, '–')}“")
            sel.table(_conflict_table(c.existing, c.incoming), key=f"conf_tab_{c.id}")
            b1, b2 = st.columns(2)
            if b1.button("Vorhandenen Wert behalten", key=f"conf_keep_{c.id}"):
                _try(lambda c=c: repo.resolve_conflict(c.id, ConflictResolution.KEEP_EXISTING),
                     "Vorhandener Wert behalten.")  # fmt: skip
            if b2.button("Neuen Wert übernehmen", key=f"conf_use_{c.id}"):
                _try(lambda c=c: repo.resolve_conflict(c.id, ConflictResolution.USE_INCOMING),
                     "Neuer Wert übernommen.")  # fmt: skip
    if len(conflicts) > CONFLICT_PAGE:
        st.caption(f"Es werden die ersten {CONFLICT_PAGE} von {len(conflicts)} Konflikten angezeigt.")
    if len(conflicts) > 1:
        with st.expander(f"Alle {len(conflicts)} Konflikte auf einmal auflösen"):
            ok = st.checkbox("Ich möchte alle offenen Konflikte dieses Raums gleich behandeln", key=f"conf_all_ok_{room.id}")
            c1, c2 = st.columns(2)
            for col, label, res in ((c1, "Überall vorhandene Werte behalten", ConflictResolution.KEEP_EXISTING),
                                    (c2, "Überall neue Werte übernehmen", ConflictResolution.USE_INCOMING)):  # fmt: skip
                if col.button(label, disabled=not ok, key=f"conf_all_{res.value}_{room.id}"):
                    _try(lambda res=res: [repo.resolve_conflict(c.id, res) for c in conflicts],
                         f"{len(conflicts)} Konflikte aufgelöst.")  # fmt: skip


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _overview_table(rooms: list[Room], tz: str) -> None:
    repo = state.repo()
    rows = []
    for r in rooms:
        sensors = repo.list_sensors(r.id, include_archived=True)
        first, last, n = sel.combined_extent([s.id for s in sensors])
        conflicts = sum(len(repo.list_conflicts(s.id)) for s in sensors)
        rows.append({
            "Raum": r.name,
            "Status": "archiviert" if r.is_archived else "aktiv",
            "Sensoren": len(sensors),
            "Messzeitraum": sel.period_label(first, last, tz) if n else "–",
            "Messungen": fmt_number(n),
            "Offene Konflikte": conflicts,
            "Beschreibung": r.description or "",
        })  # fmt: skip
    sel.table(pd.DataFrame(rows), key="rooms_overview")


def _body() -> None:
    ui.page_header(
        "Räume & Sensoren", "Räume anlegen, Sensoren zuordnen, Importe und Konflikte verwalten.", eyebrow="Daten"
    )
    _flash()
    repo = state.repo()
    tz = state.analysis_settings().timezone
    all_rooms = repo.list_rooms(include_archived=True)
    _create_room(all_rooms)
    if not all_rooms:
        ui.empty_state("Noch keine Räume", "Lege oben einen Raum an oder importiere direkt eine CSV-Datei.")
        sel.nav_link("import", "Zum Import")
        return
    show_archived = st.toggle("Archivierte Räume anzeigen", key="rooms_show_archived")
    rooms = all_rooms if show_archived else [r for r in all_rooms if not r.is_archived]
    if not rooms:
        ui.callout("info", "Alle Räume sind archiviert", "Zum Anzeigen „Archivierte Räume anzeigen“ einschalten.")
        return
    ui.section_header("Übersicht")
    _overview_table(rooms, tz)

    ui.section_header("Raum bearbeiten")
    room = sel.room_selector(rooms, key="rooms_pick", label="Raum")
    if room is None:
        return
    n_conf = sum(len(repo.list_conflicts(s.id)) for s in repo.list_sensors(room.id, include_archived=True))
    tabs = st.tabs(["Raum", "Sensoren", "Importprotokolle", f"Konflikte ({n_conf})"])
    with tabs[0]:
        _tab_room(room, all_rooms)
    with tabs[1]:
        _tab_sensors(room, [r for r in all_rooms if not r.is_archived], tz)
    with tabs[2]:
        _tab_imports(room, tz)
    with tabs[3]:
        _tab_conflicts(room, tz)


def render() -> None:
    sel.guarded(_body)
