# SwitchBot Climate Dashboard

Klima-Analyseplattform für Wohnräume auf Basis von CSV-Exporten der SwitchBot-Thermo-Hygrometer.
Die App liest die Messdaten zuverlässig ein und wertet Temperatur, Luftfeuchtigkeit, Taupunkt,
VPD und absolute Luftfeuchtigkeit transparent aus. Aus den Auswertungen entstehen
professionelle, schreibgeschützte **Klimaberichte**, die sich per Link teilen lassen, zum
Beispiel mit Vermieter oder Hausverwaltung.

> **Status:** frühe Version (0.1.0). Die Anwendung ist lokal lauffähig und mit automatisierten
> Tests abgesichert. Die Anbindung an Supabase ist implementiert, aber noch **nicht** gegen ein
> echtes Supabase-Projekt getestet (siehe [Stand und Einschränkungen](#stand-und-einschränkungen)).

## Funktionen

**Import**
- Import-Assistent in 8 Schritten. Mehrere CSV-Dateien lassen sich gleichzeitig importieren,
  und jede Datei ist eindeutig einem Raum und einem Sensor zugeordnet.
- Der Assistent erkennt Kodierung, Trennzeichen (`,` `;` Tab `|`), Dezimalkomma bzw.
  -punkt, Spaltennamen (SwitchBot-Varianten, deutsche Bezeichnungen, beliebige Reihenfolge),
  Zeitformate und Fahrenheit.
- Sommer- und Winterzeit werden korrekt behandelt. Die Original-Zeitstempel bleiben erhalten.
- Das Messintervall ist wählbar (1/15/30/60 min, 12/24 h, automatisch oder
  benutzerdefiniert). Es dient der Plausibilitätsprüfung, der Datenabdeckung und der
  Lückenerkennung.
- Gefundene Probleme werden nach Fehler, Warnung und Hinweis sortiert. Ungültige Zeilen
  zeigt der Assistent vor dem Import an.
- Deduplizierung: Wiederholte oder überlappende Exporte erzeugen keine doppelten Messungen.
  Abweichende Werte zum selben Zeitpunkt werden als **Konflikt** dokumentiert und nie
  stillschweigend überschrieben.
- Zu jedem Import entsteht ein Protokoll. Schlägt ein Import fehl, wird er vollständig
  zurückgerollt.

**Räume und Sensoren**
- Raumnamen sind frei wählbar; es gibt keine vorgegebenen Kategorien. Ein Name aus dem
  Dateinamen ist nur ein Vorschlag.
- Ähnliche, bereits vorhandene Räume werden zur Wiederverwendung angeboten, aber nie
  automatisch zusammengeführt.
- Stabile interne IDs: Umbenennen beschädigt keine Daten. Räume und Sensoren lassen sich
  archivieren und, nach Bestätigung, löschen.

**Analysen**
- Alle Kennzahlen sind **zeitgewichtet**, das heißt unregelmäßige Messabstände verfälschen
  nichts. Datenlücken werden sichtbar gemacht und nie aufgefüllt.
- Feuchtebereiche, zusammenhängende Feuchtephasen (Dauer, Häufigkeit; kurze Ausschläge
  getrennt von anhaltenden Phasen) sowie Tag- und Nacht-Vergleiche.
- Taupunktabstand und optional eine **angenommene** Oberflächentemperatur, klar als Annahme
  gekennzeichnet.
- Transparente Einschätzung in vier Stufen, ohne erfundenen „Schimmel-Score“. Die Kriterien
  sind offengelegt, Einschränkungen werden immer genannt.
- Monats- und Saisonübersichten. Unvollständige Zeiträume sind gekennzeichnet.
- Vergleiche: Tag, Woche oder Monat mit dem Vorzeitraum, zwei freie Zeiträume, gleich lange
  Zeiträume, gleicher Monat im Vorjahr, Jahreszeiten sowie Raum gegen Raum.
- Datenqualität: Zeitabdeckung, Messvollständigkeit, Lückenliste und Abgleich importierter
  mit berechneten Werten.

**Berichte und Freigaben**
- Eigene, ruhig gestaltete Berichtsansicht für Empfänger, ohne Anmeldung, ohne
  Bedienelemente der Verwaltung und für das Smartphone optimiert.
- Jeder Bericht wird als unveränderlicher **Schnappschuss** mit Versionsnummer gespeichert.
  Neue Importe ändern geteilte Berichte nicht; die App zeigt an, wenn sich die Datenbasis
  geändert hat.
- Freigabelinks mit 256-Bit-Zufallstoken (gespeichert nur als SHA-256-Hash), widerrufbar
  und optional mit Ablaufdatum.
- PDF-Export (A4, mehrseitig, mit Diagrammen, Tabellen und Seitenzahlen).

## Technik

Python 3.12 · Streamlit · Plotly · pandas · Supabase/PostgreSQL (Row Level Security) ·
fpdf2 und matplotlib (PDF) · pytest · ruff · GitHub Actions

```
app.py                  Einstieg und Routing (öffentlicher Bericht ?r=… oder interne App)
climate_dashboard/
  ingest/               CSV-Erkennung, Validierung, Intervalle, Deduplizierung, Import
  analysis/             Physik, Datenqualität, Statistik, Feuchte, Taupunkt, Zeiträume, Einschätzung
  storage/              Repository-Schnittstelle, SQLite (lokal), Supabase (online)
  reports/              Berichts-Schnappschüsse, Texte, Freigabe-Tokens, PDF
  auth/                 Anmeldung
  ui/                   Designsystem, Diagramme, Seiten
supabase/migrations/    Datenbankschema mit Zugriffsregeln
docs/                   Architektur, Formeln, Einrichtung
tests/                  automatisierte Tests (nur synthetische Daten)
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Formeln und Bewertungslogik:
[docs/FORMELN.md](docs/FORMELN.md)

## Lokal starten

Voraussetzung ist Python 3.12.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m streamlit run app.py
```

Unter macOS und Linux steht `.venv/bin/python` statt `.venv/Scripts/python`.

Ohne Supabase-Zugangsdaten läuft die App im **lokalen Entwicklungsmodus**. Die Daten liegen
dann in `data/dev.sqlite` auf diesem Rechner; die App zeigt das deutlich an.

Tests ausführen:

```bash
.venv/Scripts/python -m pytest
```

## Online betreiben

Für einen dauerhaften Betrieb, auch wenn dein eigener PC ausgeschaltet ist:

1. Datenbank einrichten: [docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md)
2. App veröffentlichen: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

Beim kostenlosen Supabase-Tarif pausiert das Projekt nach 7 Tagen ohne Nutzung. Solange es
pausiert, funktionieren auch geteilte Berichtslinks nicht. Details stehen in der
Einrichtungsanleitung.

## Datenschutz

- Echte Messdaten (CSV-Exporte), lokale Datenbanken und Zugangsdaten sind per `.gitignore`
  ausgeschlossen. Ein Test (`tests/test_repo_hygiene.py`) prüft zusätzlich, dass so etwas
  nicht ins Repository gelangt.
- Tests verwenden ausschließlich synthetische Daten.
- Berichte geben nur frei, was im Bericht steht: keine Rohdaten, keine internen IDs und
  Sensorinformationen nur auf ausdrücklichen Wunsch.
- Wer einen gültigen Freigabelink hat, kann den Bericht sehen und weitergeben. Teile Links
  deshalb nur gezielt und widerrufe sie, wenn sie nicht mehr gebraucht werden.
- Die öffentliche Berichtsseite lädt keine Schriften oder Skripte von Drittanbietern.

## Wichtiger fachlicher Hinweis

Die Auswertungen beruhen auf Raumluftmessungen an der Position des Sensors. Ob tatsächlich
Kondensation oder Schimmelbefall vorliegt, lässt sich daraus **weder feststellen noch
ausschließen**. Die Berichte ersetzen keine Begutachtung vor Ort.

## Stand und Einschränkungen

- Automatisiert getestet: Import, Analysen, Berichte, PDF, lokale Datenbank, Freigabelogik
  und das Rendern aller Seiten. Siehe GitHub Actions.
- **Nicht** gegen echtes Supabase getestet: Datenbankmigration, Zugriffsregeln (RLS) und
  Anmeldung. Dieser Code ist nur mit simulierten Clients und statischen SQL-Prüfungen
  abgesichert. Nach der Einrichtung empfiehlt sich der Selbsttest aus
  `docs/SUPABASE_SETUP.md`.
- Ein Dark Mode ist noch nicht umgesetzt.

## Lizenz

Für dieses Projekt ist derzeit **keine Lizenz** festgelegt. Der Quellcode ist öffentlich
einsehbar, aber ohne Lizenz ist die Weiterverwendung rechtlich nicht erlaubt.

## Mitwirken und Sicherheit

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)
