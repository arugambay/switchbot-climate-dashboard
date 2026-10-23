# Änderungsprotokoll

Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [0.1.0] – unveröffentlicht

### Geändert
- Neues „Studio“-Design (ersetzt das Glas-Design; nur Darstellung): hellgrauer Grund, weiße Karten
  mit großen Rundungen, Serifen-Seitentitel, neu aufgebaute Übersicht mit Raumzustand-Hauptkarte,
  Streifen-Skalen für Temperatur und Feuchte, Befundliste nach Dringlichkeit, kompakte
  Feuchtephasen-Karte, Pill-Reiter, Primäraktionen in Tiefschwarz. Berechnungen, Warnregeln,
  Import, Speicherung und Freigaben unverändert.
- UI/UX-Redesign (nur Darstellung, keine fachlichen Änderungen): neues Designsystem mit kühlem
  Grundton, weißen Karten, Versal-Labels, großen Kennzahlen mit Farbpunkt je Messgröße,
  Warnungen mit Akzentkante, Diagrammen als Karten, hervorgehobener aktiver Navigation,
  neuer Schrittanzeige im Import und Kontextleiste in der Übersicht. Grundgestaltung über die
  offiziellen Streamlit-Theme-Optionen; visuell inspiriert von Google-Stitch-Entwürfen.
- Glas-Design im Apple-Stil (nur Darstellung): farbiger Verlaufshintergrund, milchige
  Glasflächen mit Unschärfe für Karten, Diagramme, Seitenleiste und Aufklappbereiche,
  Segment-Auswahl und Tabs im iOS-Stil, Pill-Buttons, getönte Warnflächen, Systemschrift
  (SF Pro / Segoe UI Variable). Gilt auch für die öffentliche Berichtsansicht.

### Hinzugefügt
- CSV-Import-Assistent mit Formaterkennung, Messintervall-Auswahl, Validierung,
  Deduplizierung, Konfliktdokumentation, Importprotokoll und Rollback.
- Freie Raumbenennung mit Ähnlichkeitshinweisen; Verwaltung von Räumen und Sensoren.
- Zeitgewichtete Klimaanalysen: Feuchtebereiche und -phasen, Taupunkt und mögliche
  Kondensationsbedingungen, transparente Einschätzung, Monats- und Saisonübersichten,
  Tag und Nacht, Datenqualität, Veränderung ab einem Zeitpunkt.
- Vergleiche von Zeiträumen, Vorjahresmonaten, Jahreszeiten sowie Räumen und Sensoren.
- Versionierte Berichts-Schnappschüsse, öffentliche Berichtsansicht, widerrufbare
  Freigabelinks mit optionalem Ablaufdatum, PDF-Export.
- Lokaler Entwicklungsmodus (SQLite) und Supabase-Anbindung mit Row Level Security
  (Migration noch nicht gegen echtes Supabase getestet).
- Designsystem, deutsche Formatierung, automatisierte Tests, GitHub Actions.
