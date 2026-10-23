"""Deduplizierung: neue Messungen, identische Duplikate und Konflikte erkennen.

Schlüssel einer Messung ist (Sensor, Zeitpunkt). Der Sensor ist implizit (alle Daten in
``incoming``/``existing`` gehören zum selben Sensor), der Zeitpunkt wird für den Vergleich
auf volle Sekunden abgeschnitten.

* Gleiche Werte (gerundet auf 3 Nachkommastellen, Toleranz 1e-6, ``NaN == NaN``) → Duplikat.
* Abweichende Werte → Konflikt; vorhandene Werte werden nie überschrieben.
* Innerhalb der Datei: identische Doppelzeilen → eine behalten; gleicher Zeitpunkt mit
  unterschiedlichen Werten → keine der Zeilen wird importiert.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import METRICS, TS

ROUND_DECIMALS = 3
TOLERANCE = 1e-6
_KEY = "_key"


def normalize_ts(ts: pd.Series) -> pd.Series:
    """Zeitpunkt als Schlüssel: UTC, auf volle Sekunden abgeschnitten, Auflösung ns."""
    s = pd.to_datetime(ts, utc=True)
    return s.dt.floor("s").astype("datetime64[ns, UTC]")


def _key(ts: pd.Series) -> np.ndarray:
    """Zeitpunkt als int64 (ns seit Epoche, UTC)."""
    return pd.DatetimeIndex(ts).as_unit("ns").asi8


def _values_dict(row: pd.Series | dict[str, Any], metrics: Sequence[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for m in metrics:
        v = row.get(m)
        out[m] = None if v is None or pd.isna(v) else float(v)
    return out


def _equal(a: pd.DataFrame, b: pd.DataFrame, metrics: Sequence[str]) -> np.ndarray:
    """Zeilenweiser Vergleich zweier gleich langer DataFrames über ``metrics``."""
    eq = np.ones(len(a), dtype=bool)
    for m in metrics:
        x = a[m].to_numpy(dtype="float64").round(ROUND_DECIMALS)
        y = b[m].to_numpy(dtype="float64").round(ROUND_DECIMALS)
        both_nan = np.isnan(x) & np.isnan(y)
        with np.errstate(invalid="ignore"):
            close = np.abs(x - y) <= TOLERANCE
        eq &= both_nan | close
    return eq


@dataclass
class ConflictItem:
    """Abweichende Werte für denselben Zeitpunkt (vorhanden vs. neu)."""

    ts: pd.Timestamp
    existing: dict[str, float | None]
    incoming: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts.isoformat(), "existing": self.existing, "incoming": self.incoming}


@dataclass
class InFileConflict:
    """Gleicher Zeitpunkt mehrfach in der Datei, aber mit unterschiedlichen Werten."""

    ts: pd.Timestamp
    lines: list[int]
    variants: list[dict[str, float | None]]

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts.isoformat(), "lines": self.lines, "variants": self.variants}


@dataclass
class DedupResult:
    """Ergebnis der Klassifikation.

    * ``new``: zu importierende Zeilen (kanonische Spalten, ``ts`` auf Sekunden normalisiert).
    * ``duplicates``: Zeilen, die identisch bereits vorhanden sind.
    * ``conflicts``: abweichende Werte gegenüber vorhandenen Messungen.
    * ``in_file_duplicates``: Anzahl entfernter identischer Doppelzeilen in der Datei.
    * ``in_file_conflicts`` / ``in_file_conflict_rows``: Zeitpunkte mit widersprüchlichen
      Zeilen in der Datei bzw. Anzahl der dadurch nicht importierten Zeilen.
    """

    new: pd.DataFrame
    duplicates: pd.DataFrame
    conflicts: list[ConflictItem] = field(default_factory=list)
    in_file_duplicates: int = 0
    in_file_conflicts: list[InFileConflict] = field(default_factory=list)
    in_file_conflict_rows: int = 0
    compared_metrics: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "new": int(len(self.new)),
            "duplicate": int(len(self.duplicates)),
            "conflict": int(len(self.conflicts)),
            "in_file_duplicates": int(self.in_file_duplicates),
            "in_file_conflicts": int(len(self.in_file_conflicts)),
            "in_file_conflict_rows": int(self.in_file_conflict_rows),
        }

    def to_dict(self, max_items: int = 50) -> dict[str, Any]:
        return {
            **self.counts,
            "compared_metrics": list(self.compared_metrics),
            "conflicts": [c.to_dict() for c in self.conflicts[:max_items]],
            "in_file_conflict_examples": [c.to_dict() for c in self.in_file_conflicts[:max_items]],
        }


def classify(
    incoming: pd.DataFrame,
    existing: pd.DataFrame | None,
    compare_metrics: Sequence[str] | None = None,
) -> DedupResult:
    """Klassifiziert ``incoming`` gegenüber ``existing`` (beide kanonisch, gleicher Sensor).

    ``compare_metrics``: zu vergleichende Messgrößen. Standard: alle Größen, die in
    ``incoming`` mindestens einen Wert haben – fehlt einer Datei z. B. die VPD-Spalte, führt
    das nicht zu Scheinkonflikten mit bereits vorhandenen VPD-Werten.
    Der Index von ``incoming`` wird als Zeilennummer interpretiert (für Meldungen).
    """
    inc = incoming.copy()
    for m in METRICS:
        if m not in inc:
            inc[m] = np.nan
    if compare_metrics is None:
        compare_metrics = [m for m in METRICS if inc[m].notna().any()]
    metrics = list(compare_metrics)
    inc[TS] = normalize_ts(inc[TS])
    inc[_KEY] = _key(inc[TS])
    lines = pd.Series(inc.index, index=inc.index)

    # --- innerhalb der Datei ---------------------------------------------
    rounded = inc[metrics].round(ROUND_DECIMALS)
    exact_dup = pd.concat([inc[[_KEY]], rounded], axis=1).duplicated(keep="first")
    in_file_duplicates = int(exact_dup.sum())
    unique_rows = inc.loc[~exact_dup]
    key_dup = unique_rows[_KEY].duplicated(keep=False)
    in_file_conflicts: list[InFileConflict] = []
    conflict_rows = 0
    if key_dup.any():
        conflicted_keys = set(unique_rows.loc[key_dup, _KEY])
        all_conflicted = inc[inc[_KEY].isin(conflicted_keys)]
        conflict_rows = len(all_conflicted)
        # Doppelte Zeilen innerhalb eines Konflikts nicht doppelt zählen
        in_file_duplicates -= int(exact_dup[all_conflicted.index].sum())
        for key_value, grp in unique_rows.loc[key_dup].groupby(_KEY, sort=True):
            in_file_conflicts.append(
                InFileConflict(
                    ts=grp[TS].iloc[0],
                    lines=[int(x) for x in lines[all_conflicted.index[all_conflicted[_KEY] == key_value]]],
                    variants=[_values_dict(r, metrics) for _, r in grp.iterrows()],
                )
            )
        unique_rows = unique_rows.loc[~key_dup]

    # --- gegen vorhandene Messungen ------------------------------------
    conflicts: list[ConflictItem] = []
    out_cols = [TS, *METRICS] + [c for c in incoming.columns if c not in (TS, *METRICS, _KEY)]
    if existing is None or len(existing) == 0:
        new = unique_rows
        duplicates = unique_rows.iloc[0:0]
    else:
        ex = existing.copy()
        for m in METRICS:
            if m not in ex:
                ex[m] = np.nan
        ex[_KEY] = _key(normalize_ts(ex[TS]))
        ex = ex.drop_duplicates(_KEY, keep="first").set_index(_KEY)
        present = unique_rows[_KEY].isin(ex.index)
        new = unique_rows.loc[~present]
        matched = unique_rows.loc[present]
        ex_aligned = ex.loc[matched[_KEY].to_numpy()]
        eq = _equal(matched, ex_aligned, metrics)
        duplicates = matched.loc[eq]
        diff_inc = matched.loc[~eq]
        diff_ex = ex_aligned.loc[~eq]
        rec_in = diff_inc[list(METRICS)].to_dict("records")
        rec_ex = diff_ex[list(METRICS)].to_dict("records")
        for ts, r_in, r_ex in zip(diff_inc[TS], rec_in, rec_ex, strict=True):
            conflicts.append(
                ConflictItem(
                    ts=ts, existing=_values_dict(r_ex, METRICS), incoming=_values_dict(r_in, METRICS)
                )
            )
    return DedupResult(
        new=new[[c for c in out_cols if c in new.columns]],
        duplicates=duplicates[[c for c in out_cols if c in duplicates.columns]],
        conflicts=conflicts,
        in_file_duplicates=in_file_duplicates,
        in_file_conflicts=in_file_conflicts,
        in_file_conflict_rows=conflict_rows,
        compared_metrics=metrics,
    )
