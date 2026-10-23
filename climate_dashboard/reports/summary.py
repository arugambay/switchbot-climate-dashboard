"""Zusammenfassung und Fazit eines Berichts in Laiensprache.

Regeln (Grundregel 6, docs/FORMELN.md):

* Jeder Satz beruht auf Zahlen aus den Analyseergebnissen; ohne Datengrundlage kein Satz.
* Sachlich, ohne Dramatisierung und ohne Kausalbehauptungen („weil gelüftet wurde …“).
* Es wird nie behauptet, dass Schimmel oder Tauwasser tatsächlich vorhanden ist.
* Fachbegriffe werden beim ersten Auftreten kurz erklärt.
* Einschränkungen (Lücken, fehlende Oberflächentemperatur, kein Schimmelnachweis) werden
  ausdrücklich genannt.

Eingaben sind die bereits serialisierten Abschnitte des Snapshots (``to_dict()``-Form), damit
Texte auch aus gespeicherten Snapshots nachvollziehbar bleiben.
"""

from __future__ import annotations

from typing import Any

from climate_dashboard.ui.formatting import (
    fmt_date_range,
    fmt_duration,
    fmt_number,
    fmt_percent,
)

DEW_POINT_EXPLAINED = "Taupunkt (Temperatur, ab der Feuchtigkeit an Oberflächen kondensieren kann)"
HUMIDITY_NOTE_PCT = 10.0  # ab diesem Zeitanteil ≥ „erhöht“ wird die Feuchte erwähnt (wie assessment)
SPREAD_NOTE_PCT = 5.0
MAX_SENTENCES = 6

LEVEL_BY_STATUS = {
    "unauffaellig": "ok",
    "feuchte": "hinweis",
    "kondensation": "hinweis",
    "schimmel": "auffaellig",
    "keine_daten": "neutral",
}

NO_MOULD_STATEMENT = (
    "Ob an Wänden oder Fenstern tatsächlich Feuchtigkeit oder Schimmel auftritt, lässt sich aus "
    "Raumluftmessungen weder feststellen noch ausschließen."
)


def _get(d: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _n(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _deg(x: Any, decimals: int = 1) -> str:
    return f"{fmt_number(x, decimals)} °C"


def build_summary(
    *,
    meta: dict[str, Any],
    quality: dict[str, Any] | None,
    kpis: dict[str, Any] | None,
    humidity: dict[str, Any] | None,
    condensation: dict[str, Any] | None,
    assessment: dict[str, Any] | None,
) -> list[str]:
    """3–6 sachliche Sätze zur Zusammenfassung (nur datengestützte Aussagen)."""
    tz = meta.get("timezone") or "Europe/Berlin"
    th = meta.get("thresholds") or {}
    el = _n(th.get("humidity_elevated")) or 60.0
    warn = _n(th.get("dew_point_spread_warn")) or 3.0
    sentences: list[str] = []
    period = fmt_date_range(meta.get("period_start"), meta.get("period_end"), tz, end_exclusive=True)
    cov = _n(_get(quality, "time_coverage_pct"))
    n = int(_get(quality, "n") or 0)

    if n < 2:
        sentences.append(f"Für den Zeitraum {period} liegen zu wenige Messwerte für eine Auswertung vor.")
        sentences.append(NO_MOULD_STATEMENT)
        return sentences

    first = f"Ausgewertet wurde der Zeitraum {period}."
    if cov is not None:
        first += f" Für etwa {fmt_percent(cov)} dieses Zeitraums liegen Messwerte vor."
    sentences.append(first)

    t = _get(kpis, "temperature") or {}
    if _n(t.get("mean")) is not None:
        sentences.append(
            f"Die Raumtemperatur lag im Mittel bei {_deg(t.get('mean'))} "
            f"(niedrigster Wert {_deg(t.get('min'))}, höchster {_deg(t.get('max'))})."
        )

    h = _get(kpis, "humidity") or {}
    bands = _get(humidity, "bands") or {}
    pe = _n(bands.get("pct_at_or_above_elevated"))
    if pe is None and assessment:
        pe = _n(_get(assessment, "basis", "pct_elevated"))
    es = _get(humidity, "elevated_summary") or {}
    sustained = int(es.get("count_sustained") or _get(assessment, "basis", "elevated_sustained") or 0)
    if _n(h.get("mean")) is not None:
        text = (f"Die relative Luftfeuchtigkeit lag im Mittel bei {fmt_percent(h.get('mean'))} "
                f"(zwischen {fmt_percent(h.get('min'))} und {fmt_percent(h.get('max'))}).")
        if pe is not None and pe >= HUMIDITY_NOTE_PCT:
            text += (f" Etwa {fmt_percent(pe)} der gemessenen Zeit lag sie bei {fmt_percent(el)} oder darüber")
            if sustained:
                longest = fmt_duration(es.get("longest_duration_s"))
                text += (f"; {sustained} solcher Phasen hielten mindestens "
                         f"{fmt_duration((es.get('min_sustained_minutes') or 120) * 60)} an "
                         f"(die längste etwa {longest}).")
            else:
                text += "."
        elif sustained:
            times = "einmal" if sustained == 1 else f"{sustained}-mal"
            text += (f" Werte von {fmt_percent(el)} oder mehr traten insgesamt selten auf, hielten aber {times} "
                     f"mindestens {fmt_duration((es.get('min_sustained_minutes') or 120) * 60)} an "
                     f"(die längste Phase etwa {fmt_duration(es.get('longest_duration_s'))}).")
        elif pe is not None and pe > 0:
            text += f" Werte von {fmt_percent(el)} oder mehr traten nur selten und kurz auf."
        else:
            text += f" Werte von {fmt_percent(el)} oder mehr kamen nicht vor."
        sentences.append(text)

    pw = _n(_get(condensation, "pct_below_warn"))
    if pw is None:
        pw = _n(_get(assessment, "basis", "pct_spread_below_warn"))
    spread = _get(kpis, "dew_point_spread") or {}
    if pw is not None and pw >= SPREAD_NOTE_PCT:
        sentences.append(
            f"In etwa {fmt_percent(pw)} der Zeit lag die Raumtemperatur weniger als "
            f"{fmt_number(warn, 1)} K über dem {DEW_POINT_EXPLAINED}. Oberflächen, die um diesen Betrag "
            "kälter als die Raumluft sind, etwa Fensterscheiben oder Außenwandecken, könnten dann "
            "rechnerisch feucht werden."
        )
    elif _n(spread.get("mean")) is not None:
        sentences.append(
            f"Der {DEW_POINT_EXPLAINED} lag meist deutlich unter der Raumtemperatur; der Abstand betrug "
            f"im Mittel {fmt_number(spread.get('mean'), 1)} K."
        )

    surface = _get(condensation, "surface")
    if surface and surface.get("basis") == "assumed":
        hours = _n(surface.get("hours_at_or_below_dew_point")) or 0.0
        part = (f"wäre an einer Oberfläche, die {fmt_number(surface.get('offset_k'), 1)} K kälter als die "
                "Raumluft ist, ")
        if hours >= 1.0:
            sentences.append(f"Unter der Annahme des Erstellers {part}insgesamt etwa "
                             f"{fmt_number(hours, 1)} Stunden rechnerisch Tauwasser möglich gewesen "
                             "(Berechnung, keine Beobachtung).")
        else:
            crit = _n(surface.get("hours_surface_rh_critical")) or 0.0
            extra = (f"; die rechnerische Feuchte an dieser Oberfläche lag aber insgesamt etwa "
                     f"{fmt_number(crit, 0)} Stunden bei 80 % oder darüber" if crit >= 1.0 else "")
            sentences.append(f"Unter der Annahme des Erstellers {part}rechnerisch kein Tauwasser "
                             f"entstanden{extra} (Berechnung, keine Beobachtung).")

    rating = _get(quality, "rating")
    if rating and rating != "gut":
        sentences.append(
            f"Die Messdaten weisen größere Lücken auf (Zeitabdeckung {fmt_percent(cov)}); die Aussagen "
            "sind deshalb mit Unsicherheit verbunden."
        )
    return sentences[:MAX_SENTENCES]


def build_conclusion(
    *,
    assessment: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    condensation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Kurzes Fazit für Laien: ``{"level", "status", "headline", "text", "limitations"}``."""
    overall = _get(assessment, "overall") or {}
    key = overall.get("key") or "keine_daten"
    uncertain = bool(overall.get("uncertain"))
    surface = _get(condensation, "surface")
    assumed = bool(surface and surface.get("basis") == "assumed")

    if key == "unauffaellig":
        headline = "Unauffällige Messwerte"
        text = ("Im ausgewerteten Zeitraum zeigen die Messwerte keine auffälligen Bedingungen bei "
                "Luftfeuchtigkeit und Taupunkt.")
    elif key == "feuchte":
        headline = "Zeitweise erhöhte Luftfeuchtigkeit"
        text = ("Die Luftfeuchtigkeit war in diesem Zeitraum häufig oder lange erhöht. Solche Bedingungen "
                "können an kühlen Oberflächen Feuchteprobleme begünstigen. Eine Ursache lässt sich aus den "
                "Messwerten nicht ableiten.")
    elif key == "kondensation":
        headline = "Rechnerisch mögliche Kondensation"
        text = ("Für eine kalte Oberfläche" + (" (Annahme des Erstellers)" if assumed else "")
                + " wären zeitweise rechnerisch Bedingungen für Tauwasser gegeben. Das ist eine Berechnung, "
                "keine Beobachtung.")
    elif key == "schimmel":
        headline = "Anhaltend hohe Luftfeuchtigkeit über mehrere Tage"
        text = ("An mehreren Tagen innerhalb einer Woche blieb die Luftfeuchtigkeit über viele Stunden hoch. "
                "Solche Bedingungen können Schimmelwachstum an kühlen Oberflächen begünstigen. Ob es "
                "tatsächlich dazu kommt, lässt sich nur vor Ort beurteilen.")
    else:
        headline = "Keine Einschätzung möglich"
        text = "Für diesen Zeitraum liegen zu wenige Messwerte für eine Einschätzung vor."
    if uncertain and key != "keine_daten":
        text += " Wegen Lücken in den Messdaten ist diese Einschätzung unsicher."

    limitations = [NO_MOULD_STATEMENT]
    if not surface:
        limitations.append("Die Temperatur von Wand- und Fensteroberflächen wurde nicht gemessen; eine "
                           "Aussage über Tauwasser an Oberflächen ist deshalb nur eingeschränkt möglich.")
    limitations.append("Die Messung gilt für den Ort des Sensors im Raum.")
    return {
        "status": key,
        "status_label": overall.get("label"),
        "level": LEVEL_BY_STATUS.get(key, "neutral"),
        "uncertain": uncertain,
        "headline": headline,
        "text": text,
        "limitations": limitations,
    }
