# Architektur

Dieses Dokument ist der verbindliche Vertrag zwischen den Modulen. Änderungen an den hier
beschriebenen Schnittstellen müssen hier nachgezogen werden.

## Überblick

```
app.py                         Einstieg: Routing (öffentlicher Bericht ?r=<token> vs. interne App)
climate_dashboard/
  config.py                    Konfiguration, Modus local/supabase, Schutz vor Service-Role-Key
  domain/models.py             Domänenobjekte (Room, Sensor, ImportRecord, Report, Share …)
  domain/rooms.py              Raumnamen normalisieren, ähnliche Räume finden, Namensvorschlag
  ingest/                      CSV-Erkennung, Validierung, Intervalle, Deduplizierung, Import-Service
  analysis/                    Physik (Taupunkt, VPD, abs. Feuchte), Statistik, Feuchte-Phasen,
                               Kondensation, Datenqualität, Zeiträume/Vergleiche, Einschätzung
  storage/                     Repository-Schnittstelle + SQLite (lokal) + Supabase (Produktion)
  reports/                     Snapshot-Erzeugung, Fazit-Texte, Freigabe-Tokens, PDF
  auth/                        Anmeldung (Supabase Auth) bzw. lokaler Einzelnutzer
  ui/                          Designsystem, Komponenten, Diagramme, Formatierung, Seiten
supabase/migrations/           Versionierte SQL-Migrationen (Tabellen, Constraints, Indizes, RLS)
tests/                         Pytest; nur synthetische Daten (tests/synthetic.py)
```

## Grundregeln (gelten für alle Module)

1. **Rohdaten bleiben unverändert.** Importierte Werte und Original-Zeitstempeltext werden
   gespeichert wie gelesen. Berechnete Größen werden nie in die Originalspalten geschrieben.
2. **Keine erfundenen Werte.** Lücken werden nicht aufgefüllt/interpoliert. Diagramme zeigen
   Lücken sichtbar (Linie unterbrochen).
3. **Zeit:** intern immer tz-aware UTC. CSV-Zeitstempel ohne Zeitzone werden in der
   Sensor-Zeitzone (Standard `Europe/Berlin`) interpretiert. Gruppierungen nach Tag/Monat/
   Jahreszeit/Tag-Nacht erfolgen in lokaler Zeit.
4. **Zeitgewichtung:** Dauern und Zeitanteile werden über die Zeit zwischen Messungen
   berechnet, nicht über die Anzahl Messungen (unregelmäßige Intervalle!). Jede Messung
   repräsentiert die Zeit bis zur nächsten Messung, höchstens jedoch bis zur Lückengrenze
   (`gap_threshold = max(gap_factor × erwartetes Intervall, min_gap_minutes)`); längere
   Abstände gelten als Datenlücke (unbekannt) und zählen weder zu „auffällig“ noch zu „unauffällig“.
5. **Datenabdeckung** wird zweifach angegeben. Einzige Implementierung: `analysis.quality`
   (`gap_threshold_s`, `durations`, `assess_quality`); der Import (`ingest.intervals`) ruft sie auf,
   damit Import-Vorschau und Analyse identische Zahlen zeigen.
   - *Zeitraum* = angefragter Zeitraum `[start, end)`; ohne Vorgabe erste Messung bis letzte
     Messung + erwartetes Intervall (die letzte Messung repräsentiert ein Intervall).
   - *Zeitabdeckung* = durch Messungen repräsentierte Zeit (Regel 4: Abstand zur nächsten Messung
     bzw. bei Lücken/letzter Messung ein nominales Intervall) ÷ Zeitraum.
   - *Messvollständigkeit* = Anzahl Messungen ÷ (Zeitraum ÷ erwartetes Intervall), max. 100 %.
     Eine lückenlose Reihe mit n Messungen ergibt so genau 100 % (Zeitraum = n × Intervall).
   - Lückenparameter kommen aus `AnalysisSettings` (`gap_factor`, `min_gap_minutes`).
6. **Vorsichtige Sprache.** Keine Aussage über tatsächlichen Schimmel oder tatsächliche
   Kondensation aus Raumluftdaten. Kein wissenschaftlich validierter Risiko-Score. Hinweise
   unterscheiden: Beobachtung → mögliche Bedingung → Einschränkung.
7. **Deutsche Oberfläche** (Texte, Zahlen `1.234,5`, Datum `22.10.2026 21:45`).
8. **Sicherheit:** kein Service-Role-Key in App/Repo/Logs; Freigabe-Tokens nur als SHA-256-Hash
   gespeichert; öffentlicher Zugriff ausschließlich über `resolve_share` (in Supabase: RPC-Funktion
   `get_shared_report(token)` mit SECURITY DEFINER, die nur den Snapshot liefert).

## Kanonischer Messdaten-DataFrame

Definiert in `domain/models.py`: Spalte `ts` (datetime64, UTC) und
`temperature`, `humidity`, `dew_point`, `vpd`, `abs_humidity` (float64, NaN = fehlt).
Optional `raw_timestamp` (str) und in Mehr-Sensor-Abfragen `sensor_id`.
Berechnete Ergänzungen tragen das Präfix `calc_`.

Alle Ergebnis-Dataclasses der Fachmodule bieten `to_dict()` mit JSON-serialisierbaren Werten
(Zeit als ISO-8601-String mit Offset, NaN → `None`), damit sie in Berichts-Snapshots landen können.

## Modul-Schnittstellen

### ingest
- `csv_reader.parse_csv(content: bytes, file_name: str, timezone="Europe/Berlin") -> ParsedCsv`
  erkennt Kodierung (UTF-8/UTF-8-BOM/UTF-16/cp1252), Trennzeichen (`,` `;` Tab), Dezimalzeichen,
  Spalten (Aliase, beliebige Reihenfolge), Zeitstempelformat; liefert kanonische Daten,
  ungültige Zeilen und `issues`.
- `validation.Issue(severity: Severity[ERROR|WARNING|INFO], code, message, count, examples)`
- `intervals.analyze_intervals(ts, expected_s | None, settings: AnalysisSettings) -> IntervalReport`
  (erwartetes/beobachtetes Intervall, Lücken, Abdeckung, Intervallwechsel).
  `INTERVAL_PRESETS` für die Auswahl im Import-Assistenten.
- `dedup.classify(incoming, existing) -> DedupResult` (neu / identisch vorhanden / Konflikt;
  ebenso Duplikate und Konflikte innerhalb der Datei).
- `service.preview_import(...)` und `service.commit_import(...)` orchestrieren den Import mit
  Importprotokoll, Konfliktspeicherung und Rollback bei Fehlern.

### analysis
- `physics`: Magnus-Formel (Alduchov & Eskridge 1996: a=17,62, b=243,12 °C, 6,112 hPa).
  `dew_point`, `saturation_vapor_pressure`, `absolute_humidity`, `vpd`; `with_calculated(df)`
  ergänzt `calc_*`-Spalten; `effective(df)` liefert Werte mit Herkunft (gemessen/berechnet);
  `plausibility(df)` meldet Abweichungen importiert vs. berechnet über Toleranz.
- `quality`: Messdauern/Gewichte, Lücken, Abdeckung für beliebigen Zeitraum.
- `stats`: zeitgewichtete Kennzahlen (Mittel, Min, Max inkl. Zeitpunkt).
- `humidity`: Zeitanteile in Feuchtebereichen, zusammenhängende Phasen (Dauer, Max),
  Häufigkeit, Unterscheidung kurzer Ausschläge vs. anhaltender Phasen.
- `condensation`: Abstand Temperatur–Taupunkt, optional angenommene Oberflächentemperatur.
- `periods`: Aggregation (Stunde/Tag/Woche/Monat), Monats-, Saison-, Tag/Nacht-Übersichten
  mit Abdeckung und Kennzeichnung unvollständiger Zeiträume, Vergleich zweier Zeiträume
  (absolut + prozentual), Vorjahresmonat, gleich lange Zeiträume.
- `assessment`: transparente Einschätzung → Liste `Finding(level, category, text, evidence)`
  plus `limitations`.

### storage
Siehe `storage/base.py` (`Repository`-Protokoll). `storage/factory.get_repository(settings,
session)` liefert die passende Implementierung.

### reports
- `builder.build_snapshot(df, *, room, sensors, period_start, period_end, options: ReportOptions, settings,
  expected_s, version=None, created_at=None) -> dict`; `builder.data_fingerprint(df, sensor_ids, start, end)`
- `summary`: Zusammenfassung und Fazit aus Analyseergebnissen (Laiensprache).
- `sharing`: `generate_token()`, `hash_token()`, `share_url()`.
- `pdf.render_pdf(snapshot) -> bytes`.

## Berichts-Snapshot

Ein Bericht wird bei Erstellung/Aktualisierung vollständig berechnet und als JSON in
`report_versions.content` gespeichert. Neue Importe ändern bestehende Versionen nie.
Freigabelinks zeigen die `current_version` des Berichts; diese ändert sich nur durch eine
bewusste Aktion („Neue Version erstellen“). `data_fingerprint` (Hash über Sensor-IDs,
Zeitraum, Anzahl und Prüfsumme der Messungen) macht sichtbar, ob sich die Datenbasis seit
der Version geändert hat.

Struktur (`schema_version: 1`) – Details in `reports/builder.py`:
`meta`, `options`, `quality`, `kpis`, `humidity`, `condensation`, `assessment`, `monthly`,
`seasonal`, `day_night`, `series` (stündlich aggregiert – über 2 Jahre 3-stündlich, über 5 Jahre täglich –, Lücken als `null`;
Zeitachse als `t_start` + `step_s`), `summary`, `conclusion`.
