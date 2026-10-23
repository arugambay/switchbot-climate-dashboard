"""CSV-Einlesen: Kodierung, Trennzeichen, Dezimalzeichen, Spalten und Zeitstempel erkennen.

Ergebnis ist ein :class:`ParsedCsv` mit dem kanonischen Messdaten-DataFrame (siehe
``domain/models.py``), den ungültigen Zeilen und verständlichen Meldungen. Nach außen wird
keine Exception geworfen: Probleme erscheinen als ``Issue`` (``ERROR`` blockiert den Import).

Grundsätze:

* Zeitstempel werden nie verschoben. Nicht existierende Ortszeiten (Sprung auf Sommerzeit)
  werden als ungültig markiert; doppelt vorkommende Ortszeiten (Rückstellung im Herbst)
  werden über die Reihenfolge in der Datei aufgelöst.
* Der Originaltext jedes Zeitstempels bleibt in ``raw_timestamp`` erhalten.
* Alles ist vektorisiert (pandas), damit auch Dateien mit Millionen Zeilen zügig laufen.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import (
    HUMIDITY,
    METRIC_LABELS,
    METRICS,
    RAW_TIMESTAMP,
    TEMPERATURE,
    TS,
)
from climate_dashboard.ingest.columns import DATE_ROLE, TIME_ROLE, TIMESTAMP_ROLE, ColumnMapping, map_columns
from climate_dashboard.ingest.validation import MAX_EXAMPLES, Issue, Severity, check_plausibility, has_errors

DELIMITER_CANDIDATES: tuple[str, ...] = ("\t", ";", "|", ",")  # Reihenfolge = Vorrang bei Gleichstand
DELIMITER_NAMES: dict[str, str] = {
    ",": "Komma",
    ";": "Semikolon",
    "\t": "Tabulator",
    "|": "senkrechter Strich",
}
_NA_TOKENS = {"", "nan", "null", "none", "-", "--", "n/a", "na", "#n/a", "#wert!", "#value!"}
_SAMPLE_SIZE = 2000

INVALID_COLUMNS = ("line", "reason", "raw")


# ---------------------------------------------------------------------------
# Ergebnisobjekt
# ---------------------------------------------------------------------------
def empty_measurements() -> pd.DataFrame:
    """Leerer kanonischer DataFrame (inkl. ``raw_timestamp``)."""
    df = pd.DataFrame(
        {
            TS: pd.Series([], dtype="datetime64[ns, UTC]"),
            **{m: pd.Series([], dtype="float64") for m in METRICS},
            RAW_TIMESTAMP: pd.Series([], dtype="object"),
        }
    )
    df.index.name = "line"
    return df


def _empty_invalid() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "line": pd.Series([], dtype="int64"),
            "reason": pd.Series([], dtype="object"),
            "raw": pd.Series([], dtype="object"),
        }
    )


@dataclass
class ParsedCsv:
    """Ergebnis von :func:`parse_csv`.

    * ``data``: kanonische Messdaten (``ts`` UTC, Messgrößen float64, ``raw_timestamp``),
      nach ``ts`` sortiert; der Index ist die Zeilennummer in der Datei (1-basiert, Kopfzeile
      mitgezählt).
    * ``invalid_rows``: DataFrame mit ``line`` (Zeilennummer), ``reason`` (Grund, deutsch),
      ``raw`` (Rohtext der Zeile).
    * ``rows_read``: Anzahl nicht leerer Datenzeilen (ohne Kopfzeile). Es gilt
      ``rows_read == len(data) + len(invalid_rows)``.
    * ``empty_lines``: ignorierte Leerzeilen.
    * ``detection``: erkannte Parameter (Kodierung, Trennzeichen, Dezimalzeichen, Spalten …).
    """

    file_name: str
    sha256: str
    data: pd.DataFrame = field(default_factory=empty_measurements)
    invalid_rows: pd.DataFrame = field(default_factory=_empty_invalid)
    rows_read: int = 0
    empty_lines: int = 0
    issues: list[Issue] = field(default_factory=list)
    detection: dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return has_errors(self.issues)

    @property
    def metrics_present(self) -> list[str]:
        """Messgrößen, die als Spalte in der Datei vorhanden waren."""
        return list(self.detection.get("metrics_present", []))

    def to_dict(self, max_invalid: int = 50) -> dict[str, Any]:
        ts = self.data[TS] if len(self.data) else None
        return {
            "file_name": self.file_name,
            "sha256": self.sha256,
            "rows_read": int(self.rows_read),
            "rows_valid": int(len(self.data)),
            "rows_invalid": int(len(self.invalid_rows)),
            "empty_lines": int(self.empty_lines),
            "ts_min": ts.min().isoformat() if ts is not None else None,
            "ts_max": ts.max().isoformat() if ts is not None else None,
            "issues": [i.to_dict() for i in self.issues],
            "detection": self.detection,
            "invalid_rows": [
                {"line": int(r.line), "reason": str(r.reason), "raw": str(r.raw)}
                for r in self.invalid_rows.head(max_invalid).itertuples()
            ],
        }


# ---------------------------------------------------------------------------
# Kodierung
# ---------------------------------------------------------------------------
def decode_bytes(content: bytes) -> tuple[str, str, list[Issue]]:
    """Dekodiert den Dateiinhalt. Rückgabe: (Text, Name der Kodierung, Meldungen)."""
    issues: list[Issue] = []
    if content.startswith(codecs.BOM_UTF8):
        try:
            return content[3:].decode("utf-8"), "UTF-8 mit BOM", issues
        except UnicodeDecodeError:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "encoding_errors",
                    "Einige Zeichen der Datei konnten nicht gelesen werden und wurden ersetzt.",
                )
            )
            return content[3:].decode("utf-8", errors="replace"), "UTF-8 mit BOM", issues
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return content.decode("utf-16", errors="replace"), "UTF-16", issues
    # UTF-16 ohne BOM: viele Nullbytes an jeder zweiten Stelle
    if len(content) >= 4:
        sample = content[:4000]
        half = max(len(sample) // 2, 1)
        if sample[1::2].count(0) > 0.4 * half:
            return content.decode("utf-16-le", errors="replace"), "UTF-16 (LE, ohne BOM)", issues
        if sample[0::2].count(0) > 0.4 * half:
            return content.decode("utf-16-be", errors="replace"), "UTF-16 (BE, ohne BOM)", issues
    try:
        return content.decode("utf-8"), "UTF-8", issues
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp1252"), "Windows-1252", issues
    except UnicodeDecodeError:
        return content.decode("latin-1"), "ISO-8859-1", issues


def _looks_binary(text: str) -> bool:
    sample = text[:4000]
    if not sample:
        return False
    bad = sum(1 for ch in sample if (ord(ch) < 32 and ch not in "\t\r\n") or ch == "�")
    return bad / len(sample) > 0.1


# ---------------------------------------------------------------------------
# Trennzeichen
# ---------------------------------------------------------------------------
def _count_outside_quotes(line: str, ch: str) -> int:
    count = 0
    in_quotes = False
    for c in line:
        if c == '"':
            in_quotes = not in_quotes
        elif c == ch and not in_quotes:
            count += 1
    return count


def detect_delimiter(header: str, sample_lines: list[str]) -> str:
    """Erkennt das Trennzeichen.

    Kriterium: Die Anzahl Trennzeichen (außerhalb von Anführungszeichen) ist in der Kopfzeile
    gleich dem häufigsten Wert in den Datenzeilen und in möglichst allen Datenzeilen gleich.
    So wird z. B. ``"19,2"`` in einer Komma-Datei ebenso korrekt behandelt wie Dezimalkommas
    in einer Semikolon-Datei.
    """
    lines = [ln for ln in sample_lines if ln.strip()]
    best: tuple[float, int, str] | None = None
    for priority, cand in enumerate(DELIMITER_CANDIDATES):
        h = _count_outside_quotes(header, cand)
        counts = [_count_outside_quotes(ln, cand) for ln in lines] or [h]
        values, freq = np.unique(counts, return_counts=True)
        mode = int(values[np.argmax(freq)])
        consistency = float(freq.max()) / len(counts)
        if mode < 1 and h < 1:
            continue
        score = consistency + (1.0 if h == mode else 0.0)
        key = (score, -priority, cand)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else ","


# ---------------------------------------------------------------------------
# Zahlen
# ---------------------------------------------------------------------------
def _to_float(text: pd.Series) -> pd.Series:
    """Schnelle Umwandlung (``astype``); bei nicht lesbaren Werten Fallback mit NaN."""
    try:
        return text.astype("float64")
    except (ValueError, TypeError):
        return pd.to_numeric(text, errors="coerce").astype("float64")


def _parse_numeric(raw: pd.Series) -> tuple[pd.Series, str, pd.Series, pd.Series]:
    """Wandelt Text in Zahlen um (Dezimalkomma und -punkt, auch gemischt).

    Rückgabe: (Werte, Dezimal-Beschreibung, Maske „nicht lesbar“, Maske „leer“).
    Leer sind leere Zellen und Platzhalter wie ``NaN``, ``null``, ``-``.
    """
    s = raw.str.strip()
    empty = s == ""
    has_comma = s.str.contains(",", regex=False)
    has_dot = s.str.contains(".", regex=False)
    norm = s.str.replace(",", ".", regex=False) if bool(has_comma.any()) else s
    both = has_comma & has_dot
    if bool(both.any()):  # „1.234,5“ – Punkt als Tausendertrenner
        norm = norm.mask(both, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    values = _to_float(norm.mask(empty))
    bad = values.isna() & ~empty
    if bool(bad.any()):
        # Platzhalter für „kein Wert“ zählen als leer
        token = s[bad].str.lower().isin(_NA_TOKENS)
        empty.loc[token[token].index] = True
        bad = values.isna() & ~empty
    if bool(bad.any()):
        # zweiter Versuch: Unicode-Minus und Einheit am Ende (z. B. „19,2 °C“, „55 %“)
        retry = (
            norm[bad]
            .str.replace("−", "-", regex=False)
            .str.replace(r"\s*(°\s*[CcFf]|℃|℉|%|kPa|g/m³|g/m3)$", "", regex=True)
        )
        values.loc[bad] = pd.to_numeric(retry, errors="coerce").astype("float64")
        bad = values.isna() & ~empty
    n_comma = int((has_comma & ~has_dot & ~empty).sum())
    n_dot = int((has_dot & ~has_comma & ~empty).sum())
    if n_comma and n_dot:
        decimal = "gemischt"
    elif n_comma:
        decimal = "Komma"
    elif n_dot:
        decimal = "Punkt"
    else:
        decimal = "ganzzahlig"
    return values, decimal, bad, empty


# ---------------------------------------------------------------------------
# Zeitstempel
# ---------------------------------------------------------------------------
_RE_EPOCH = re.compile(r"^\d{9,13}(\.\d+)?$")
_RE_ISO_TZ = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(Z|[+-]\d{2}(:?\d{2})?)$"
)
_RE_ISO = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_RE_DE = re.compile(r"^\d{1,2}\.\d{1,2}\.(\d{4}|\d{2})(,?\s+\d{1,2}:\d{2}(:\d{2})?)?$")
_RE_SLASH = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}(,?\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?)?$")

_ISO_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)
_DE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y, %H:%M:%S",
    "%d.%m.%Y, %H:%M",
    "%d.%m.%y %H:%M:%S",
    "%d.%m.%y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y",
)


def _slash_formats(day_first: bool) -> tuple[str, ...]:
    d = "%d/%m/%Y" if day_first else "%m/%d/%Y"
    return (
        f"{d} %H:%M:%S",
        f"{d} %H:%M",
        f"{d} %I:%M:%S %p",
        f"{d} %I:%M %p",
        f"{d}, %H:%M:%S",
        f"{d}, %H:%M",
        d,
    )


_FORMAT_LABELS = {
    "%Y-%m-%d %H:%M:%S": "JJJJ-MM-TT hh:mm:ss",
    "%Y-%m-%d %H:%M": "JJJJ-MM-TT hh:mm",
    "%Y-%m-%dT%H:%M:%S": "JJJJ-MM-TTThh:mm:ss",
    "%Y-%m-%dT%H:%M": "JJJJ-MM-TTThh:mm",
    "%d.%m.%Y %H:%M:%S": "TT.MM.JJJJ hh:mm:ss",
    "%d.%m.%Y %H:%M": "TT.MM.JJJJ hh:mm",
    "%d/%m/%Y %H:%M:%S": "TT/MM/JJJJ hh:mm:ss",
    "%d/%m/%Y %H:%M": "TT/MM/JJJJ hh:mm",
    "%m/%d/%Y %H:%M:%S": "MM/TT/JJJJ hh:mm:ss",
    "%m/%d/%Y %H:%M": "MM/TT/JJJJ hh:mm",
}


def _parse_with_formats(s: pd.Series, formats: tuple[str, ...]) -> tuple[pd.Series, list[str]]:
    """Parst mit einer Liste fester Formate; jede Zeile mit dem ersten passenden Format."""
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    todo = s.notna()
    used: list[str] = []
    for fmt in formats:
        if not todo.any():
            break
        parsed = pd.to_datetime(s[todo], format=fmt, errors="coerce")
        ok = parsed.notna()
        if ok.any():
            used.append(fmt)
            result.loc[ok[ok].index] = parsed[ok].astype("datetime64[ns]")
            todo.loc[ok[ok].index] = False
    return result, used


def _order_score(ts: pd.Series) -> tuple[int, float]:
    """(Anzahl Rückwärtssprünge, Median-Abstand in s) in Dateireihenfolge – kleiner = plausibler."""
    v = ts.dropna()
    if len(v) < 2:
        return 0, 0.0
    d = v.diff().dt.total_seconds().iloc[1:]
    return int((d < 0).sum()), float(d.abs().median())


@dataclass
class _TsResult:
    utc: pd.Series
    reason: pd.Series  # str oder None je Zeile
    label: str
    issues: list[Issue]
    localized: bool


def _localize(naive: pd.Series, tz: str) -> tuple[pd.Series, pd.Series, list[Issue]]:
    """Lokalisiert naive Ortszeiten (Dateireihenfolge!) ohne Zeitstempel zu verschieben.

    Rückgabe: (UTC-Zeitstempel, Maske nicht existierender Zeiten, Meldungen).
    """
    issues: list[Issue] = []
    idx = pd.DatetimeIndex(naive.to_numpy()).as_unit("ns")
    n = len(idx)
    valid = ~idx.isna()
    dst = idx.tz_localize(tz, ambiguous=np.ones(n, dtype=bool), nonexistent="NaT").as_unit("ns")
    std = idx.tz_localize(tz, ambiguous=np.zeros(n, dtype=bool), nonexistent="NaT").as_unit("ns")
    nonexistent = valid & dst.isna()
    ambiguous = valid & ~dst.isna() & (dst.asi8 != std.asi8)
    choose_dst = np.ones(n, dtype=bool)
    if ambiguous.any():
        pos = np.flatnonzero(ambiguous)
        days = idx[pos].normalize().asi8
        resolved = 0
        unresolved = 0
        for day in np.unique(days):
            p = pos[days == day]
            vals = idx[p].asi8
            # Erste Belegung = Sommerzeit; sobald die Ortszeit zurückspringt (oder sich
            # wiederholt), beginnt die zweite Belegung = Normalzeit.
            cummax = np.maximum.accumulate(vals)
            back = np.flatnonzero(vals[1:] <= cummax[:-1])
            if len(back):
                split = int(back[0]) + 1
                choose_dst[p[split:]] = False
                resolved += len(p)
            else:
                unresolved += len(p)
        if resolved:
            issues.append(
                Issue(
                    Severity.INFO,
                    "dst_ambiguous_resolved",
                    "Durch die Zeitumstellung im Herbst gibt es eine Stunde doppelt. Die betroffenen "
                    "Messungen wurden anhand ihrer Reihenfolge in der Datei richtig zugeordnet.",
                    resolved,
                )
            )
        if unresolved:
            ex = [str(t) for t in idx[pos][:MAX_EXAMPLES]]
            issues.append(
                Issue(
                    Severity.WARNING,
                    "dst_ambiguous_unresolved",
                    "Einige Zeitpunkte liegen in der doppelten Stunde der Herbst-Zeitumstellung und "
                    "ließen sich nicht eindeutig zuordnen. Sie wurden als Sommerzeit (erste Belegung) "
                    "interpretiert.",
                    unresolved,
                    ex,
                )
            )
    utc_vals = np.where(choose_dst, dst.asi8, std.asi8)
    utc = pd.Series(pd.DatetimeIndex(utc_vals.view("datetime64[ns]")).tz_localize("UTC"), index=naive.index)
    utc[~valid | nonexistent] = pd.NaT
    return utc, pd.Series(nonexistent, index=naive.index), issues


def parse_timestamps(raw: pd.Series, tz: str) -> _TsResult:
    """Erkennt das Zeitformat und wandelt in UTC um.

    Unterstützt: ISO (mit/ohne Sekunden, mit ``T``, mit Offset/``Z`` → nicht lokalisiert),
    ``TT.MM.JJJJ hh:mm[:ss]``, ``TT/MM/JJJJ`` bzw. ``MM/TT/JJJJ`` (automatisch unterschieden),
    Unix-Zeit in Sekunden oder Millisekunden.
    """
    issues: list[Issue] = []
    s = raw.str.strip()
    empty = s == ""
    candidates = s.str.len() <= 6  # Platzhalter wie „NaN“, „null“, „-“ nur bei kurzen Texten prüfen
    if bool(candidates.any()):
        empty |= candidates & s.str.lower().isin(_NA_TOKENS)
    s = s.mask(empty, None)
    reason = pd.Series(None, index=s.index, dtype="object")
    reason[empty] = "Zeitstempel fehlt"
    utc = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")

    sample = s.dropna().head(_SAMPLE_SIZE).tolist()
    scores = {
        "epoch": sum(bool(_RE_EPOCH.match(v)) for v in sample),
        "iso_tz": sum(bool(_RE_ISO_TZ.match(v)) for v in sample),
        "iso": sum(bool(_RE_ISO.match(v)) for v in sample),
        "de": sum(bool(_RE_DE.match(v)) for v in sample),
        "slash": sum(bool(_RE_SLASH.match(v)) for v in sample),
    }
    family = max(scores, key=lambda k: scores[k]) if sample else "iso"
    if sample and scores[family] == 0:
        reason[s.notna()] = "Zeitstempel nicht lesbar"
        issues.append(
            Issue(
                Severity.ERROR,
                "timestamp_format_unknown",
                "Das Format der Zeitstempel wurde nicht erkannt.",
                int(s.notna().sum()),
                [str(v) for v in sample[:MAX_EXAMPLES]],
            )
        )
        return _TsResult(utc, reason, "unbekannt", issues, False)

    label = ""
    localized = True
    naive = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if family == "epoch":
        num = pd.to_numeric(s, errors="coerce")
        unit = "ms" if num.max() > 1e11 else "s"
        utc = pd.to_datetime(num, unit=unit, utc=True, errors="coerce").astype("datetime64[ns, UTC]")
        label = f"Unix-Zeit ({'Millisekunden' if unit == 'ms' else 'Sekunden'}, UTC)"
        localized = False
    else:
        tz_rows = pd.Series(False, index=s.index)
        if scores["iso_tz"]:
            tz_rows = s.notna() & s.str.contains(r"(?:Z|[+-]\d{2}:?\d{2}|[+-]\d{2})$", regex=True, na=False)
            tz_rows &= s.str.contains(r"^\d{4}-", regex=True, na=False)
            utc_part = pd.to_datetime(s[tz_rows], format="ISO8601", utc=True, errors="coerce")
            utc.loc[tz_rows] = utc_part.astype("datetime64[ns, UTC]")
        rest = s.where(~tz_rows)
        if family in ("iso", "iso_tz"):
            naive, used = _parse_with_formats(rest, _ISO_FORMATS)
        elif family == "de":
            naive, used = _parse_with_formats(rest, _DE_FORMATS)
        else:
            day_first, used_d = _parse_with_formats(rest, _slash_formats(True))
            month_first, used_m = _parse_with_formats(rest, _slash_formats(False))
            n_d, n_m = int(day_first.notna().sum()), int(month_first.notna().sum())
            if n_d != n_m:
                use_day = n_d > n_m
            else:
                sd, sm = _order_score(day_first), _order_score(month_first)
                if sd != sm and n_d:
                    use_day = sd < sm
                else:
                    use_day = True
                    issues.append(
                        Issue(
                            Severity.WARNING,
                            "date_order_ambiguous",
                            "Bei den Datumsangaben mit Schrägstrich ist nicht eindeutig, ob zuerst der Tag "
                            "oder der Monat steht. Es wurde Tag/Monat/Jahr angenommen – bitte prüfen.",
                        )
                    )
            naive, used = (day_first, used_d) if use_day else (month_first, used_m)
        label = ", ".join(_FORMAT_LABELS.get(f, f) for f in used) or "unbekannt"
        if tz_rows.any():
            label = (label + " + " if used else "") + "ISO 8601 mit Zeitzonen-Angabe"
            issues.append(
                Issue(
                    Severity.INFO,
                    "timestamp_with_offset",
                    "Die Zeitstempel enthalten eine Zeitzonen-Angabe und wurden direkt übernommen.",
                    int(tz_rows.sum()),
                )
            )
        naive_rows = naive.notna()
        if naive_rows.any():
            loc, nonexistent, loc_issues = _localize(naive[naive_rows], tz)
            utc.loc[naive_rows] = loc
            issues.extend(loc_issues)
            if nonexistent.any():
                bad_idx = nonexistent[nonexistent].index
                reason.loc[bad_idx] = "Zeitpunkt existiert wegen der Umstellung auf Sommerzeit nicht"
                issues.append(
                    Issue(
                        Severity.WARNING,
                        "dst_nonexistent",
                        "Einige Zeitstempel liegen in der Stunde, die bei der Umstellung auf Sommerzeit "
                        "übersprungen wird. Diese Zeitpunkte gibt es nicht; die Zeilen wurden nicht "
                        "übernommen (Zeitstempel werden nie verschoben).",
                        len(bad_idx),
                        [f"Zeile {i}: {raw.loc[i]}" for i in bad_idx[:MAX_EXAMPLES]],
                    )
                )
            label += f" (Ortszeit {tz})"

    unreadable = s.notna() & utc.isna() & reason.isna()
    if unreadable.any():
        reason[unreadable] = "Zeitstempel nicht lesbar"
        issues.append(
            Issue(
                Severity.WARNING,
                "timestamp_unreadable",
                "Einige Zeitstempel konnten nicht gelesen werden; diese Zeilen wurden nicht übernommen.",
                int(unreadable.sum()),
                [f"Zeile {i}: {raw.loc[i]}" for i in unreadable[unreadable].index[:MAX_EXAMPLES]],
            )
        )
    return _TsResult(utc, reason, label, issues, localized)


# ---------------------------------------------------------------------------
# Tabelle lesen
# ---------------------------------------------------------------------------
def _read_table(body: str, delimiter: str, ncols: int) -> tuple[pd.DataFrame, pd.Series]:
    """Liest die Datenzeilen als Text. Rückgabe: (DataFrame mit Spalten 0..ncols-1, Maske
    „Zeile hat mehr Felder als die Kopfzeile“)."""
    try:
        df = pd.read_csv(
            io.StringIO(body),
            sep=delimiter,
            header=None,
            names=list(range(ncols)),
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=False,
            quotechar='"',
            skipinitialspace=True,
            engine="c",
            index_col=False,
        )
        df = df.fillna("")
        return df, pd.Series(False, index=df.index)
    except (pd.errors.ParserError, ValueError):
        pass
    rows: list[list[str]] = []
    too_many: list[bool] = []
    for fields in csv.reader(io.StringIO(body), delimiter=delimiter, quotechar='"', skipinitialspace=True):
        too_many.append(len(fields) > ncols)
        rows.append((fields + [""] * ncols)[:ncols])
    df = pd.DataFrame(rows, columns=list(range(ncols)), dtype=str)
    return df, pd.Series(too_many, index=df.index)


def _raw_lines(rows: pd.DataFrame, delimiter: str) -> list[str]:
    """Rohtext der (wenigen) ungültigen Zeilen, aus den Feldern wieder zusammengesetzt."""
    return [
        delimiter.join(str(v) for v in values).rstrip(delimiter) for values in rows.itertuples(index=False)
    ]


def _metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def parse_csv(content: bytes, file_name: str, timezone: str = "Europe/Berlin") -> ParsedCsv:
    """Liest eine CSV-Datei robust ein (siehe Moduldokumentation).

    Wirft keine Exceptions: auch völlig unlesbare Inhalte führen zu einem ``ERROR``-Issue.
    """
    sha = hashlib.sha256(content).hexdigest()
    result = ParsedCsv(file_name=file_name, sha256=sha)
    try:
        _parse_into(result, content, timezone)
    except Exception as exc:  # letzte Absicherung – nie eine Exception nach außen
        result.data = empty_measurements()
        result.invalid_rows = _empty_invalid()
        result.issues.append(
            Issue(
                Severity.ERROR,
                "unreadable",
                f"Die Datei konnte nicht gelesen werden ({type(exc).__name__}). Bitte prüfen Sie, ob es "
                "sich um einen CSV-Export des Sensors handelt.",
            )
        )
    return result


def _parse_into(result: ParsedCsv, content: bytes, tz: str) -> None:
    issues = result.issues
    det = result.detection
    det["timezone"] = tz
    if not content.strip():
        issues.append(Issue(Severity.ERROR, "empty_file", "Die Datei ist leer."))
        return
    text, encoding, enc_issues = decode_bytes(content)
    issues.extend(enc_issues)
    det["encoding"] = encoding
    if _looks_binary(text):
        issues.append(
            Issue(
                Severity.ERROR,
                "not_text",
                "Die Datei ist keine lesbare Textdatei (CSV). Bitte den CSV-Export verwenden.",
            )
        )
        return
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    # Kopfzeile = erste nicht leere Zeile (optional nach Excel-Hinweis „sep=;“)
    h = 0
    while h < len(lines) and not lines[h].strip():
        h += 1
    if h >= len(lines):
        issues.append(Issue(Severity.ERROR, "empty_file", "Die Datei enthält keine Daten."))
        return
    leading_empty = h
    forced_delim: str | None = None
    m = re.fullmatch(r'\s*"?sep=(.)"?\s*', lines[h])
    if m:
        forced_delim = m.group(1)
        h += 1
        while h < len(lines) and not lines[h].strip():
            h += 1
            leading_empty += 1
        if h >= len(lines):
            issues.append(Issue(Severity.ERROR, "empty_file", "Die Datei enthält keine Daten."))
            return

    header_line = lines[h]
    delimiter = forced_delim or detect_delimiter(header_line, lines[h + 1 : h + 201])
    det["delimiter"] = delimiter
    det["delimiter_name"] = DELIMITER_NAMES.get(delimiter, repr(delimiter))
    det["header_line"] = h + 1

    headers = [c.strip() for c in next(csv.reader([header_line], delimiter=delimiter, skipinitialspace=True))]
    headers = [c or f"Spalte {i + 1}" for i, c in enumerate(headers)]
    mapping = map_columns(headers)
    det["columns"] = headers
    det["column_mapping"] = {orig: role for role, orig in mapping.roles.items()}
    det["unknown_columns"] = list(mapping.unknown)
    det["ignored_columns"] = list(mapping.ignored)
    det["metrics_present"] = mapping.metrics
    det["missing_metrics"] = mapping.missing_metrics
    det["fahrenheit_columns"] = sorted(mapping.roles[r] for r in mapping.fahrenheit)
    _column_issues(mapping, issues)

    body_lines = lines[h + 1 :]
    first_line_no = h + 2  # 1-basiert: Zeile nach der Kopfzeile
    if not body_lines:
        issues.append(
            Issue(Severity.ERROR, "no_data_rows", "Die Datei enthält nur eine Kopfzeile, keine Messwerte.")
        )
        return
    table, too_many = _read_table("\n".join(body_lines), delimiter, len(headers))
    table.index = pd.RangeIndex(first_line_no, first_line_no + len(table), name="line")
    too_many.index = table.index
    empty_row = table[0].str.strip() == ""
    for c in table.columns[1:]:  # nur Kandidaten prüfen (Spalte 0 ist fast immer gefüllt)
        cand = empty_row[empty_row].index
        if len(cand) == 0:
            break
        empty_row.loc[cand] = (table.loc[cand, c].str.strip() == "").to_numpy()
    result.empty_lines = int(empty_row.sum()) + leading_empty
    if result.empty_lines:
        issues.append(
            Issue(Severity.INFO, "empty_lines", "Leere Zeilen wurden ignoriert.", result.empty_lines)
        )
    rows = ~empty_row
    result.rows_read = int(rows.sum())
    if has_errors(issues):
        return
    if result.rows_read == 0:
        issues.append(Issue(Severity.ERROR, "no_data_rows", "Die Datei enthält keine Messwerte."))
        return

    col_pos = {h_: i for i, h_ in enumerate(headers)}
    t = table.loc[rows]

    # --- Zeitstempel ------------------------------------------------------
    roles = mapping.roles
    if TIMESTAMP_ROLE in roles:
        raw_ts = t[col_pos[roles[TIMESTAMP_ROLE]]].str.strip()
    elif DATE_ROLE in roles and TIME_ROLE in roles:
        raw_ts = t[col_pos[roles[DATE_ROLE]]].str.strip() + " " + t[col_pos[roles[TIME_ROLE]]].str.strip()
        raw_ts = raw_ts.str.strip()
    else:
        raw_ts = t[col_pos[roles.get(DATE_ROLE) or roles[TIME_ROLE]]].str.strip()
    ts_res = parse_timestamps(raw_ts, tz)
    issues.extend(ts_res.issues)
    det["timestamp_format"] = ts_res.label
    det["timestamps_localized"] = ts_res.localized
    reason = ts_res.reason.copy()
    reason[too_many & reason.isna()] = "Zeile hat mehr Felder als die Kopfzeile"

    # --- Messwerte --------------------------------------------------------
    data = pd.DataFrame(index=t.index)
    data[TS] = ts_res.utc.astype("datetime64[ns, UTC]")
    decimals: dict[str, str] = {}
    for metric in METRICS:
        if metric not in roles:
            data[metric] = np.nan
            continue
        values, decimal, bad, _empty = _parse_numeric(t[col_pos[roles[metric]]])
        decimals[metric] = decimal
        if bad.any():
            issues.append(
                Issue(
                    Severity.WARNING,
                    "value_unreadable",
                    f"{_metric_label(metric)}: Einige Werte sind keine Zahl und wurden als fehlend behandelt.",
                    int(bad.sum()),
                    [
                        f"Zeile {i}: {v}"
                        for i, v in t.loc[bad, col_pos[roles[metric]]].head(MAX_EXAMPLES).items()
                    ],
                )
            )
        if metric in mapping.fahrenheit:
            values = (values - 32.0) * 5.0 / 9.0
        data[metric] = values
    det["decimal_by_column"] = {roles[m]: d for m, d in decimals.items()}
    kinds = {d for d in decimals.values() if d != "ganzzahlig"}
    det["decimal"] = kinds.pop() if len(kinds) == 1 else ("gemischt" if kinds else "ganzzahlig")
    mixed = [roles[m] for m, d in decimals.items() if d == "gemischt"]
    if len(kinds) > 1 or mixed:
        issues.append(
            Issue(
                Severity.WARNING,
                "decimal_mixed",
                "In der Datei werden sowohl Dezimalkomma als auch Dezimalpunkt verwendet. Beide wurden "
                "als Dezimaltrennzeichen gelesen – bitte die Werte stichprobenartig prüfen.",
                len(mixed),
                mixed,
            )
        )
    if mapping.fahrenheit:
        issues.append(
            Issue(
                Severity.INFO,
                "fahrenheit_converted",
                "Temperaturwerte lagen in Grad Fahrenheit vor und wurden in Grad Celsius umgerechnet.",
                len(mapping.fahrenheit),
                det["fahrenheit_columns"],
            )
        )
    data[RAW_TIMESTAMP] = raw_ts.astype(object)

    # --- Plausibilität ----------------------------------------------------
    ts_ok = reason.isna()
    checked, plaus_issues, no_core = check_plausibility(data.loc[ts_ok])
    issues.extend(plaus_issues)
    data.loc[ts_ok, list(METRICS)] = checked[list(METRICS)]
    no_core_idx = no_core[no_core].index
    if len(no_core_idx):
        reason.loc[no_core_idx] = "Weder gültige Temperatur noch gültige Luftfeuchtigkeit"
        issues.append(
            Issue(
                Severity.WARNING,
                "row_without_values",
                "Einige Zeilen enthalten weder eine gültige Temperatur noch eine gültige Luftfeuchtigkeit "
                "und wurden nicht übernommen.",
                len(no_core_idx),
                [f"Zeile {i}" for i in no_core_idx[:MAX_EXAMPLES]],
            )
        )

    invalid_mask = reason.notna()
    result.invalid_rows = pd.DataFrame(
        {
            "line": reason.index[invalid_mask].astype("int64"),
            "reason": reason[invalid_mask].astype(object).to_numpy(),
            "raw": _raw_lines(t.loc[invalid_mask], delimiter),
        }
    )
    valid = data.loc[~invalid_mask]
    if len(valid) > 1 and not valid[TS].is_monotonic_increasing:
        issues.append(
            Issue(
                Severity.INFO,
                "unsorted",
                "Die Datei war nicht zeitlich sortiert; die Messungen wurden nach Zeit sortiert.",
            )
        )
        valid = valid.sort_values(TS, kind="mergesort")
    valid = valid[[TS, *METRICS, RAW_TIMESTAMP]]
    valid.index.name = "line"
    result.data = valid
    if len(valid) == 0:
        issues.append(
            Issue(
                Severity.ERROR,
                "no_valid_rows",
                "Die Datei enthält keine einzige gültige Messung.",
                result.rows_read,
            )
        )


def _column_issues(mapping: ColumnMapping, issues: list[Issue]) -> None:
    if not mapping.has_time:
        issues.append(
            Issue(
                Severity.ERROR,
                "no_timestamp_column",
                "Es wurde keine Spalte mit Datum/Uhrzeit gefunden (z. B. „timestamp“ oder „Datum“).",
            )
        )
    if TEMPERATURE not in mapping.roles:
        issues.append(
            Issue(
                Severity.ERROR,
                "no_temperature_column",
                "Es wurde keine Temperaturspalte gefunden (z. B. „temperature(℃)“ oder „Temperatur“).",
            )
        )
    if mapping.unknown:
        issues.append(
            Issue(
                Severity.INFO,
                "unknown_columns",
                "Einige Spalten wurden nicht erkannt und werden nicht importiert.",
                len(mapping.unknown),
                list(mapping.unknown),
            )
        )
    if mapping.ignored:
        issues.append(
            Issue(
                Severity.INFO,
                "duplicate_columns",
                "Einige Messgrößen kommen mehrfach vor; es wird jeweils nur eine Spalte verwendet.",
                len(mapping.ignored),
                list(mapping.ignored),
            )
        )
    if TEMPERATURE in mapping.roles and HUMIDITY not in mapping.roles:
        issues.append(
            Issue(
                Severity.WARNING,
                "no_humidity_column",
                "Es wurde keine Spalte für die relative Luftfeuchtigkeit gefunden. Auswertungen zur "
                "Feuchte sind für diese Daten nicht möglich.",
            )
        )
    optional_missing = [m for m in mapping.missing_metrics if m not in (TEMPERATURE, HUMIDITY)]
    if optional_missing and TEMPERATURE in mapping.roles:
        issues.append(
            Issue(
                Severity.INFO,
                "optional_metrics_missing",
                "Folgende Größen fehlen in der Datei und werden bei Bedarf aus Temperatur und "
                "Luftfeuchtigkeit berechnet: " + ", ".join(_metric_label(m) for m in optional_missing) + ".",
                len(optional_missing),
            )
        )
