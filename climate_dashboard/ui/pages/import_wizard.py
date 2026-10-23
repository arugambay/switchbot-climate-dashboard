"""Seite „Import“ – Assistent für SwitchBot-CSV-Exporte.

Ablauf (Schritte): Dateien → Raum → Sensor → Intervall → Vorschau → Prüfung → Import → Ergebnis.
Die Entscheidungslogik steht in ``ui/import_flow.py``; diese Seite hält nur Widgets und den
Sitzungszustand. Neue Räume/Sensoren werden erst beim Bestätigen angelegt.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.domain.models import METRIC_LABELS, RAW_TIMESTAMP, TS, Room, Sensor
from climate_dashboard.ingest.intervals import INTERVAL_PRESETS
from climate_dashboard.ingest.service import ImportPreview
from climate_dashboard.ingest.validation import Issue, Severity
from climate_dashboard.ui import charts, selectors, state
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import formatting as fmt
from climate_dashboard.ui import import_flow as flow

_STATE_KEY = "imp_wizard"
_LEVELS = {Severity.ERROR: "auffaellig", Severity.WARNING: "hinweis", Severity.INFO: "info"}
_SEVERITY_TITLES = {Severity.ERROR: "Fehler", Severity.WARNING: "Warnungen", Severity.INFO: "Hinweise"}
_TIMEZONES = ("Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "Europe/Paris", "UTC")
_ROOM_NEW, _ROOM_EXISTING = "Neuer Raum", "Vorhandener Raum"
_SENSOR_NEW, _SENSOR_EXISTING = "Neuer Sensor", "Vorhandener Sensor"


# ---------------------------------------------------------------------------
# Sitzungszustand
# ---------------------------------------------------------------------------
def _wizard() -> dict[str, Any]:
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = {
            "step": 0,
            "entries": [],  # list[FileEntry]
            "cache": {},  # Vorschau-Cache (Schlüssel siehe import_flow.preview_key)
            "previews": {},  # file_id -> aktuelle ImportPreview
            "outcomes": [],
            "uploader": 0,
        }
    return st.session_state[_STATE_KEY]


def _reset() -> None:
    old = st.session_state.get(_STATE_KEY, {})
    for key in [k for k in st.session_state if str(k).startswith("imp_") and k != _STATE_KEY]:
        del st.session_state[key]
    st.session_state.pop(_STATE_KEY, None)
    _wizard()["uploader"] = int(old.get("uploader", 0)) + 1


def _go(step: int) -> None:
    _wizard()["step"] = max(0, min(step, len(flow.STEPS) - 1))


def _nav(step: int, *, next_label: str = "Weiter", next_disabled: bool = False, back: bool = True) -> None:
    with st.container(horizontal=True):
        if back and step > 0:
            st.button("Zurück", key=f"imp_back_{step}", on_click=_go, args=(step - 1,))
        st.button(
            next_label,
            key=f"imp_next_{step}",
            type="primary",
            disabled=next_disabled,
            on_click=_go,
            args=(step + 1,),
        )
        st.button("Abbrechen", key=f"imp_cancel_{step}", on_click=_reset, help="Nichts wird gespeichert.")


# ---------------------------------------------------------------------------
# Daten
# ---------------------------------------------------------------------------
def _rooms_all() -> list[Room]:
    return state.repo().list_rooms(include_archived=True)


def _sensors() -> list[Sensor]:
    return state.repo().list_sensors(include_archived=False)


def _default_tz() -> str:
    try:
        return state.analysis_settings().timezone
    except Exception:  # noqa: BLE001 – Einstellungen optional
        return fmt.DEFAULT_TZ


def _assignment_table(entries: list[flow.FileEntry], rooms: list[Room], sensors: list[Sensor]) -> None:
    ui.section_header("Aktuelle Zuordnung")
    st.dataframe(
        pd.DataFrame(flow.assignment_rows(entries, rooms, sensors)), hide_index=True, width="stretch"
    )


# ---------------------------------------------------------------------------
# Schritt 1: Dateien
# ---------------------------------------------------------------------------
def _step_files(w: dict[str, Any]) -> None:
    ui.section_header(
        "CSV-Dateien wählen",
        "Exporte aus der SwitchBot-App (eine oder mehrere Dateien). Trennzeichen, Dezimalzeichen und "
        "Zeitformat werden automatisch erkannt.",
    )
    uploads = st.file_uploader(
        "CSV-Dateien",
        type=["csv", "txt", "tsv"],
        accept_multiple_files=True,
        key=f"imp_upload_{w['uploader']}",
    )
    if uploads:
        files = [(u.name, u.getvalue()) for u in uploads]
        w["entries"], skipped = flow.add_files(w["entries"], files, _default_tz())
        if skipped:
            ui.callout(
                "info", "Doppelte Datei", "Gleicher Inhalt wurde nur einmal übernommen: " + ", ".join(skipped)
            )
    entries: list[flow.FileEntry] = w["entries"]
    if not entries:
        ui.empty_state(
            "Noch keine Datei ausgewählt", "Ziehen Sie eine oder mehrere CSV-Dateien in das Feld oben."
        )
    for e in list(entries):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{e.name}** · {fmt.fmt_number(e.size / 1024, 0)} KB")
            if st.button("Entfernen", key=f"imp_rm_file_{e.file_id}"):
                w["entries"] = [x for x in entries if x.file_id != e.file_id]
                w["uploader"] += 1  # Upload-Feld leeren, damit die Datei nicht erneut erscheint
                st.rerun()
    _nav(0, next_disabled=not entries, back=False)


# ---------------------------------------------------------------------------
# Schritt 2: Raum
# ---------------------------------------------------------------------------
def _use_room(e: flow.FileEntry, room: Room) -> None:
    flow.use_existing_room(e, room)
    st.session_state[f"imp_roommode_{e.file_id}"] = _ROOM_EXISTING
    st.session_state[f"imp_roomid_{e.file_id}"] = room.id


def _room_block(e: flow.FileEntry, rooms_all: list[Room]) -> None:
    active = [r for r in rooms_all if not r.is_archived]
    fid = e.file_id
    mode_key, name_key, id_key = f"imp_roommode_{fid}", f"imp_roomname_{fid}", f"imp_roomid_{fid}"
    st.session_state.setdefault(mode_key, _ROOM_EXISTING if e.room_mode == flow.EXISTING else _ROOM_NEW)
    st.session_state.setdefault(name_key, e.new_room_name)
    with st.container(border=True):
        st.markdown(f"**{e.name}**")
        options = [_ROOM_NEW, _ROOM_EXISTING] if active else [_ROOM_NEW]
        if st.session_state[mode_key] not in options:
            st.session_state[mode_key] = _ROOM_NEW
        mode = st.radio("Raum", options, key=mode_key, horizontal=True, label_visibility="collapsed")
        if mode == _ROOM_EXISTING:
            e.room_mode = flow.EXISTING
            ids = [r.id for r in active]
            if st.session_state.get(id_key) not in ids:
                st.session_state[id_key] = e.room_id if e.room_id in ids else ids[0]
            names = {r.id: r.name for r in active}
            e.room_id = st.selectbox("Vorhandener Raum", ids, key=id_key, format_func=names.get)
        else:
            e.room_mode = flow.NEW
            e.new_room_name = st.text_input(
                "Raumname",
                key=name_key,
                max_chars=120,
                help="Frei wählbar. Der Vorschlag stammt aus dem Dateinamen und kann geändert werden.",
            )
            for room, score in flow.similar_room_hints(e, rooms_all):
                if room.is_archived:
                    ui.callout(
                        "info",
                        f"Es gibt einen archivierten Raum „{room.name}“.",
                        "Archivierte Räume können unter „Räume & Sensoren“ wiederhergestellt werden.",
                    )
                    continue
                exact = score >= 1.0
                ui.callout(
                    "hinweis",
                    f"Meinst du den vorhandenen Raum „{room.name}“?",
                    "Der Name ist identisch."
                    if exact
                    else "Der Name ist sehr ähnlich. Räume werden nie "
                    "automatisch zusammengeführt – bitte selbst entscheiden.",
                )
                st.button(
                    "Vorhandenen Raum verwenden",
                    key=f"imp_useroom_{fid}_{room.id}",
                    on_click=_use_room,
                    args=(e, room),
                )
        for msg in flow.room_errors(e, rooms_all):
            ui.callout("auffaellig", msg)


def _step_room(w: dict[str, Any]) -> None:
    ui.section_header("Raum zuordnen", "Zu welchem Raum gehören die Messwerte der jeweiligen Datei?")
    rooms_all, sensors = _rooms_all(), _sensors()
    entries: list[flow.FileEntry] = w["entries"]
    for e in entries:
        _room_block(e, rooms_all)
    _assignment_table(entries, rooms_all, sensors)
    _nav(1, next_disabled=any(flow.room_errors(e, rooms_all) for e in entries))


# ---------------------------------------------------------------------------
# Schritt 3: Sensor
# ---------------------------------------------------------------------------
def _sensor_block(e: flow.FileEntry, rooms_all: list[Room], sensors: list[Sensor]) -> None:
    fid = e.file_id
    rooms_by_id = {r.id: r for r in rooms_all}
    in_room = [s for s in sensors if e.room_mode == flow.EXISTING and s.room_id == e.room_id]
    mode_key, name_key, id_key, tz_key = (f"imp_sensormode_{fid}", f"imp_sensorname_{fid}",
                                          f"imp_sensorid_{fid}", f"imp_tz_{fid}")  # fmt: skip
    st.session_state.setdefault(
        mode_key, _SENSOR_EXISTING if (in_room and e.sensor_mode == flow.EXISTING) else _SENSOR_NEW
    )
    st.session_state.setdefault(name_key, e.new_sensor_name)
    st.session_state.setdefault(tz_key, e.timezone)
    with st.container(border=True):
        st.markdown(f"**{e.name}** → {flow.room_label(e, rooms_by_id)}")
        options = [_SENSOR_EXISTING, _SENSOR_NEW] if in_room else [_SENSOR_NEW]
        if st.session_state[mode_key] not in options:
            st.session_state[mode_key] = options[0]
        mode = st.radio("Sensor", options, key=mode_key, horizontal=True, label_visibility="collapsed")
        if mode == _SENSOR_EXISTING:
            e.sensor_mode = flow.EXISTING
            ids = [s.id for s in in_room]
            if st.session_state.get(id_key) not in ids:
                st.session_state[id_key] = e.sensor_id if e.sensor_id in ids else ids[0]
            names = {s.id: f"{s.name} ({s.timezone})" for s in in_room}
            e.sensor_id = st.selectbox("Vorhandener Sensor", ids, key=id_key, format_func=names.get)
        else:
            e.sensor_mode = flow.NEW
            e.sensor_id = None
            e.new_sensor_name = st.text_input("Sensorname", key=name_key, max_chars=120)
            tz_options = list(dict.fromkeys([st.session_state[tz_key], *_TIMEZONES]))
            e.timezone = st.selectbox(
                "Zeitzone der Zeitstempel",
                tz_options,
                key=tz_key,
                help="Die Zeitstempel im SwitchBot-Export sind Ortszeit ohne Zeitzonenangabe.",
            )
        for msg in flow.sensor_errors(e, sensors):
            ui.callout("auffaellig", msg)


def _step_sensor(w: dict[str, Any]) -> None:
    ui.section_header("Sensor wählen", "Jede Datei gehört zu genau einem Sensor.")
    rooms_all, sensors = _rooms_all(), _sensors()
    entries: list[flow.FileEntry] = w["entries"]
    for e in entries:
        _sensor_block(e, rooms_all, sensors)
    _assignment_table(entries, rooms_all, sensors)
    _nav(2, next_disabled=any(flow.sensor_errors(e, sensors) for e in entries))


# ---------------------------------------------------------------------------
# Schritt 4: Intervall
# ---------------------------------------------------------------------------
def _step_interval(w: dict[str, Any]) -> None:
    ui.section_header("Erwartetes Messintervall")
    ui.callout(
        "info",
        "Wie oft misst der Sensor?",
        "Gemeint ist die Messfrequenz (z. B. alle 15 Minuten), nicht der Zeitraum des Exports. "
        "„Automatisch erkennen“ ermittelt das typische Intervall aus den Daten. Das Intervall "
        "bestimmt, ab wann ein Abstand als Datenlücke gilt.",
    )
    sensors = {s.id: s for s in _sensors()}
    labels = [*INTERVAL_PRESETS, flow.CUSTOM_INTERVAL]
    for e in w["entries"]:
        fid = e.file_id
        label_key, value_key, unit_key = f"imp_int_{fid}", f"imp_intval_{fid}", f"imp_intunit_{fid}"
        if label_key not in st.session_state:
            sensor = sensors.get(e.sensor_id) if e.sensor_mode == flow.EXISTING else None
            if sensor is not None and sensor.expected_interval_s:
                e.interval_label, e.custom_interval_value, e.custom_interval_unit = flow.interval_label_for(
                    sensor.expected_interval_s
                )
            st.session_state[label_key] = e.interval_label
            st.session_state[value_key] = float(e.custom_interval_value)
            st.session_state[unit_key] = e.custom_interval_unit
        with st.container(border=True):
            st.markdown(f"**{e.name}**")
            e.interval_label = st.selectbox("Messintervall", labels, key=label_key)
            if e.interval_label == flow.CUSTOM_INTERVAL:
                c1, c2 = st.columns(2)
                with c1:
                    e.custom_interval_value = st.number_input("Wert", min_value=1.0, step=1.0, key=value_key)
                with c2:
                    e.custom_interval_unit = st.selectbox("Einheit", list(flow.INTERVAL_UNITS), key=unit_key)
    _nav(3, next_label="Vorschau erstellen")


# ---------------------------------------------------------------------------
# Schritt 5: Vorschau
# ---------------------------------------------------------------------------
def _compute_previews(w: dict[str, Any]) -> dict[str, ImportPreview]:
    repo, sensors, settings = state.repo(), _sensors(), state.analysis_settings()
    previews: dict[str, ImportPreview] = {}
    with ui.loading("Dateien werden geprüft …"):
        for e in w["entries"]:
            previews[e.file_id] = flow.get_preview(w["cache"], repo, e, sensors, settings)
    w["previews"] = previews
    return previews


def _local_rows(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "Zeile", out.index)
    out[TS] = out[TS].map(lambda v: fmt.fmt_datetime(v, tz, seconds=True))
    return out.rename(columns={TS: "Zeit (lokal)", RAW_TIMESTAMP: "Originaltext", **METRIC_LABELS})


def _preview_block(e: flow.FileEntry, p: ImportPreview) -> None:
    det = p.parsed.detection
    r = p.intervals
    tz = p.timezone
    ui.section_header(e.name)
    if p.parsed.has_errors:
        for issue in p.parsed.issues:
            if issue.severity == Severity.ERROR:
                ui.callout("auffaellig", issue.message)
        return
    mapping = det.get("column_mapping", {})
    ui.key_value_list(
        [
            ("Kodierung", det.get("encoding")),
            ("Trennzeichen", det.get("delimiter_name")),
            ("Dezimalzeichen", det.get("decimal")),
            ("Zeitformat", det.get("timestamp_format")),
            ("Erkannte Spalten", ", ".join(f"{k} → {METRIC_LABELS.get(v, v)}" for k, v in mapping.items())),
            ("Nicht erkannte Spalten", ", ".join(det.get("unknown_columns", [])) or "keine"),
            (
                "Fehlende Messgrößen",
                ", ".join(METRIC_LABELS.get(m, m) for m in det.get("missing_metrics", [])) or "keine",
            ),
        ]
    )
    expected_sub = {"user": "vorgegeben", "auto": "automatisch erkannt"}.get(r.expected_source or "", "")
    ui.metric_grid(
        [
            {"label": "Zeitraum", "value": fmt.fmt_date_range(r.start, r.end, tz)},
            {"label": "Messungen", "value": fmt.fmt_int(len(p.parsed.data))},
            {
                "label": "Erwartetes Intervall",
                "value": fmt.fmt_duration(r.expected_s),
                "sublabel": expected_sub,
            },
            {"label": "Beobachtet (Median)", "value": fmt.fmt_duration(r.observed_median_s)},
            {
                "label": "Zeitabdeckung",
                "value": fmt.fmt_percent(r.time_coverage_pct, 1),
                "status": ui.coverage_level(r.time_coverage_pct),
            },
            {"label": "Messvollständigkeit", "value": fmt.fmt_percent(r.completeness_pct, 1)},
            {
                "label": "Datenlücken",
                "value": fmt.fmt_int(len(r.gaps)),
                "sublabel": f"längste {fmt.fmt_duration(r.longest_gap.duration_s)}"
                if r.longest_gap
                else None,
            },
        ]
    )
    if r.deviation_text:
        ui.callout("hinweis", "Intervall weicht ab", r.deviation_text)
    data = p.parsed.data
    if len(data):
        with st.expander("Erste und letzte Zeilen"):
            st.dataframe(_local_rows(data.head(5), tz), hide_index=True, width="stretch")
            st.dataframe(_local_rows(data.tail(5), tz), hide_index=True, width="stretch")
        if len(data) > 1:
            fig = charts.timeseries_chart(
                data.reset_index(drop=True),
                tz=tz,
                gap_threshold_s=r.gap_threshold_s,
                expected_interval_s=r.expected_s,
                panel_height=160,
            )
            ui.render_chart(fig, key=f"imp_chart_{e.file_id}")


def _step_preview(w: dict[str, Any]) -> None:
    previews = _compute_previews(w)
    for e in w["entries"]:
        _preview_block(e, previews[e.file_id])
    _nav(4)


# ---------------------------------------------------------------------------
# Schritt 6: Prüfung
# ---------------------------------------------------------------------------
def _issue_text(issue: Issue) -> str | None:
    parts = []
    if issue.count:
        parts.append(f"Betroffen: {fmt.fmt_int(issue.count)}")
    if issue.examples:
        parts.append("Beispiele: " + "; ".join(issue.examples[:5]))
    return " · ".join(parts) or None


def _issues(issues: list[Issue]) -> None:
    groups = flow.group_issues(issues)
    if not any(groups.values()):
        ui.callout("ok", "Keine Auffälligkeiten gefunden.")
    for severity, items in groups.items():
        if not items:
            continue
        st.markdown(f"**{_SEVERITY_TITLES[severity]}**")
        for issue in items:
            ui.callout(_LEVELS[severity], issue.message, _issue_text(issue))


def _step_check(w: dict[str, Any]) -> None:
    previews = w["previews"] or _compute_previews(w)
    for e in w["entries"]:
        p = previews[e.file_id]
        c = p.counts
        ui.section_header(e.name)
        ui.metric_grid(
            [
                {"label": "Gelesen", "value": fmt.fmt_int(c["rows_read"])},
                {"label": "Neu", "value": fmt.fmt_int(c["rows_new"])},
                {"label": "Bereits vorhanden", "value": fmt.fmt_int(c["rows_duplicate"])},
                {"label": "Konflikte", "value": fmt.fmt_int(c["rows_conflict"])},
                {"label": "Ungültig", "value": fmt.fmt_int(c["rows_invalid"])},
            ],
            min_width=140,
        )
        if not p.checked_against_existing:
            ui.callout("info", "Neuer Sensor", "Für diesen Sensor sind noch keine Messungen gespeichert.")
        _issues(p.issues)
        if len(p.parsed.invalid_rows):
            with st.expander(f"Ungültige Zeilen ({fmt.fmt_int(len(p.parsed.invalid_rows))})"):
                st.dataframe(
                    p.parsed.invalid_rows.head(1000).rename(
                        columns={"line": "Zeile", "reason": "Grund", "raw": "Inhalt"}
                    ),
                    hide_index=True,
                    width="stretch",
                )
    multi = flow.session_issues(w["entries"], previews)
    if multi:
        ui.section_header("Mehrere Dateien")
        _issues(multi)
    _nav(5)


# ---------------------------------------------------------------------------
# Schritt 7: Import
# ---------------------------------------------------------------------------
def _step_import(w: dict[str, Any]) -> None:
    rooms_all, sensors = _rooms_all(), _sensors()
    previews = w["previews"] or _compute_previews(w)
    ui.section_header("Import bestätigen")
    _assignment_table(w["entries"], rooms_all, sensors)
    reasons = flow.blocking_reasons(w["entries"], rooms_all, sensors, previews)
    for reason in reasons:
        ui.callout("auffaellig", reason)
    new_rooms = [e.new_room_name.strip() for e in w["entries"] if e.room_mode == flow.NEW]
    if new_rooms:
        ui.callout("info", "Neue Räume werden beim Import angelegt", ", ".join(dict.fromkeys(new_rooms)))
    with st.container(horizontal=True):
        st.button("Zurück", key="imp_back_6", on_click=_go, args=(5,))
        start = st.button("Import starten", key="imp_start", type="primary", disabled=bool(reasons))
        st.button("Abbrechen", key="imp_cancel_6", on_click=_reset)
    if start and not reasons:
        progress = ui.Progress("Import läuft")
        w["outcomes"] = flow.run_import(state.repo(), w["entries"], previews, progress=progress.update)
        progress.done()
        state.invalidate_data()
        _go(7)
        st.rerun()


# ---------------------------------------------------------------------------
# Schritt 8: Ergebnis
# ---------------------------------------------------------------------------
def _step_result(w: dict[str, Any]) -> None:
    outcomes: list[flow.ImportOutcome] = w["outcomes"]
    if not outcomes:
        ui.empty_state("Kein Import durchgeführt")
    any_conflicts = False
    for o in outcomes:
        r = o.record
        ui.section_header(o.file_name)
        if r is None or o.error:
            ui.callout("auffaellig", "Import fehlgeschlagen", o.error)
            continue
        ui.callout("ok", "Import abgeschlossen", f"{fmt.fmt_int(r.rows_new)} neue Messungen gespeichert.")
        tz = fmt.DEFAULT_TZ
        ui.key_value_list(
            [
                ("Gelesene Zeilen", fmt.fmt_int(r.rows_read)),
                ("Neu", fmt.fmt_int(r.rows_new)),
                ("Bereits vorhanden", fmt.fmt_int(r.rows_duplicate)),
                ("Ungültig", fmt.fmt_int(r.rows_invalid)),
                ("Konflikte", fmt.fmt_int(r.rows_conflict)),
                ("Zeitraum", fmt.fmt_date_range(r.ts_min, r.ts_max, tz)),
                ("Erwartetes Intervall", fmt.fmt_duration(r.expected_interval_s)),
            ]
        )
        if r.warnings:
            with st.expander(f"Warnungen ({len(r.warnings)})"):
                for text in r.warnings:
                    st.markdown(f"- {text}")
        any_conflicts = any_conflicts or r.rows_conflict > 0
    if any_conflicts:
        ui.callout(
            "hinweis",
            "Abweichende Werte gefunden",
            "Für einige Zeitpunkte waren bereits andere Werte gespeichert. Nichts wurde überschrieben – "
            "die Abweichungen können unter „Räume & Sensoren“ geprüft und aufgelöst werden.",
        )
    with st.container(horizontal=True):
        selectors.nav_link("analyse", "Zur Analyse")
        selectors.nav_link("raeume", "Räume & Sensoren (Konflikte auflösen)")
    st.button("Weitere Dateien importieren", key="imp_again", type="primary", on_click=_reset)


_STEPS = (
    _step_files,
    _step_room,
    _step_sensor,
    _step_interval,
    _step_preview,
    _step_check,
    _step_import,
    _step_result,
)


def render() -> None:
    ui.page_header("Import", "SwitchBot-CSV-Exporte Schritt für Schritt einlesen.", eyebrow="Daten")
    w = _wizard()
    step = int(w["step"])
    if step > 0 and not w["entries"] and step != 7:
        step = w["step"] = 0
    ui.step_indicator(flow.STEPS, step)
    selectors.guarded(lambda: _STEPS[step](w))
