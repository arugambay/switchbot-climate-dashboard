# Funktionsumfang: SwitchBot Climate Dashboard

Vollständige Liste der Seiten, angezeigten Inhalte und Aktionen der Web-App. Nur was hier steht,
gibt es in der App. Alles andere bitte nicht in die Oberfläche aufnehmen.

## Grundsätzliches
- Web-App zur Auswertung des Raumklimas aus **importierten CSV-Dateien** von SwitchBot-Sensoren.
- Keine Live-Verbindung zu Sensoren: Alle Werte stammen aus importierten Dateien. „Aktuell“
  heißt immer „letzte importierte Messung“.
- Messgrößen: Temperatur (°C), relative Luftfeuchtigkeit (%), Taupunkt (°C),
  absolute Luftfeuchtigkeit (g/m³), VPD (kPa). Zusätzlich berechnet: Abstand Raumluft – Taupunkt (K).
- Sprache: Deutsch.
- Zwei Bereiche: **interne App** (mit Anmeldung) und **öffentlicher Bericht** (per Link, ohne
  Anmeldung, nur lesen).

## Nicht vorhanden (nicht darstellen)
Live-/Online-Status, Echtzeitwerte, Sensor koppeln, Batterie, Signal, Firmware, Außentemperatur,
Wetter, Prognosen, Suche, Benachrichtigungen, Profil-/Avatar-Menü, Registrierung,
„Passwort vergessen“, Geräte- oder Entfeuchtersteuerung, Lücken interpolieren, CO₂/Luftqualität,
Licht, Raumgröße/Stockwerk, Risiko-Prozentwerte oder Scores, Exporte außer PDF-Bericht.

---

## Navigation (interne App)
1. Übersicht
2. Import *(Gruppe Daten)*
3. Räume & Sensoren *(Gruppe Daten)*
4. Analyse *(Gruppe Auswertung)*
5. Vergleich *(Gruppe Auswertung)*
6. Berichte & Freigaben *(Gruppe Auswertung)*
7. Einstellungen *(Gruppe System)*

Außerdem:
- **Anmeldeseite** (nur im Online-Betrieb)
- **Styleguide** (nur im lokalen Entwicklungsmodus)
- **Modus-Hinweis** im lokalen Modus: „Lokaler Entwicklungsmodus – Daten werden nur lokal gespeichert“
- Produktname **Climate Dashboard**, Unterzeile **SwitchBot-Auswertung**, Logo

## Wiederkehrende Statusstufen
Unauffällig · Hinweis · Auffällig · Info · Neutral (Einschränkung)

Gesamteinschätzung, genau einer dieser Werte:
1. Gemessene Bedingungen unauffällig
2. Möglicherweise kritische Feuchtebedingungen
3. Mögliche Kondensationsbedingungen
4. Möglicherweise schimmelbegünstigende Bedingungen
5. Keine Einschätzung möglich

---

## 1. Übersicht
**Anzeige bei mehreren Räumen:**
- je Raum: Name, letzte rel. Feuchte, letzte Temperatur, Zeitpunkt der letzten Messung,
  Gesamteinschätzung (Status)
- Auswahl „Raum im Detail“

**Anzeige je Raum:**
- Raumname, Sensorname (bei mehreren Sensoren: „jüngste Daten von N Sensoren“),
  Raumbeschreibung (falls vorhanden)
- Zeitpunkt der letzten Messung
- Hinweis „Daten nicht aktuell“, wenn die letzte Messung älter als 2 Tage ist
- **Letzte Messwerte** mit Durchschnitten:
  - Temperatur, mit Ø 7 Tage und Ø 30 Tage
  - Relative Luftfeuchtigkeit, mit Ø 7 Tage und Ø 30 Tage
  - Taupunkt, mit Ø 30 Tage
  - Absolute Feuchte, mit Ø 30 Tage
  - VPD, mit Ø 30 Tage
- **Einschätzung der letzten 30 Tage:**
  - Gesamteinschätzung mit Begründungstext
  - bis zu 3 Befunde (Status, Kategorie, Text)
  - Feuchtephasen: Anzahl, davon anhaltend, Schwelle in % rF
  - Längste Feuchtephase: Dauer und Beginn
  - Zeitabdeckung in %, Messvollständigkeit in %, Anzahl Lücken
- **Diagramm** der letzten 7 Tage: Temperatur und rel. Feuchte
- Link „Ausführliche Analyse öffnen“
- **Entwicklung:** „Letzte 7 Tage vs. 7 Tage davor“ und „Vormonat vs. letzter Monat“, jeweils
  Veränderung von Temperatur (K), rel. Feuchte (Prozentpunkte) und Zeit mit erhöhter Feuchte
  (Prozentpunkte), dazu Hinweise zur Vergleichbarkeit

**Leer- und Sonderzustände:**
- keine Räume: „Noch keine Daten“ mit Link „Zum Import“
- Raum ohne Sensor
- Raum ohne Messdaten

---

## 2. Import (Assistent, 8 Schritte)
Die Schritte: **Dateien → Raum → Sensor → Intervall → Vorschau → Prüfung → Import → Ergebnis**

In jedem Schritt:
- Aktionen: Zurück, Weiter, Abbrechen (dabei wird nichts gespeichert)
- ab Schritt 2 eine Tabelle „Aktuelle Zuordnung“: Datei, Raum, Sensor, Intervall

1. **Dateien:**
   - eine oder mehrere CSV/TXT/TSV-Dateien hochladen (bis 200 MB je Datei)
   - Liste der gewählten Dateien, jede einzeln entfernbar
2. **Raum** (je Datei):
   - wählen zwischen „Neuer Raum“ (Name frei eingeben, Vorschlag aus dem Dateinamen) und „Vorhandener Raum“ (Auswahl)
   - gibt es einen ähnlichen vorhandenen Raum: Hinweis mit Aktion „Vorhandenen Raum verwenden“
   - Fehlermeldungen bei leerem, zu langem oder bereits vergebenem Namen
3. **Sensor** (je Datei):
   - „Neuer Sensor“ (Name, Zeitzone) oder „Vorhandener Sensor“ (Auswahl)
4. **Erwartetes Messintervall** (je Datei):
   - Automatisch erkennen, 1 Minute, 15 Minuten, 30 Minuten, 60 Minuten, 12 Stunden, 24 Stunden
   - oder benutzerdefiniert (Zahl und Einheit)
5. **Vorschau** (je Datei):
   - erkanntes Format: Kodierung, Trennzeichen, Dezimalzeichen, Zeitformat, Spaltenzuordnung, unbekannte Spalten
   - Kennzahlen: Zeitraum, Anzahl Messungen, erwartetes und beobachtetes Intervall, Zeitabdeckung, Messvollständigkeit, Lücken
   - erste und letzte Zeilen
   - kleines Vorschau-Diagramm
6. **Prüfung** (je Datei):
   - Anzahl neu, bereits vorhanden, Konflikte, ungültig
   - Meldungen getrennt nach Fehler (blockieren den Import), Warnungen und Hinweisen
   - Liste ungültiger Zeilen: Zeilennummer, Grund, Rohtext
   - Hinweise bei mehreren Dateien, z. B. Überlappung beim selben Sensor
7. **Import:**
   - Zusammenfassung und gegebenenfalls Gründe, die einen Import verhindern
   - Aktion „Import starten“, gesperrt bei Fehlern
   - Fortschrittsanzeige
8. **Ergebnis** (je Datei):
   - Status und Importprotokoll: gelesen, neu, bereits vorhanden, ungültig, Konflikte, Zeitraum
   - Warnungen
   - Hinweis auf Konflikte mit Link zu Räume & Sensoren, Link zur Analyse
   - Aktion „Weitere Dateien importieren“

---

## 3. Räume & Sensoren
**Raum anlegen:** Name, Beschreibung (optional). Bei einem ähnlichen Namen kann man den vorhandenen Raum verwenden, trotzdem anlegen oder abbrechen.

**Übersichtstabelle der Räume:** Raum, Status (aktiv/archiviert), Sensoren, Messzeitraum,
Messungen, offene Konflikte, Beschreibung. Archivierte Räume lassen sich ein- und ausblenden.

**Raum bearbeiten** (Raum auswählen) mit vier Bereichen:
- **Raum:**
  - umbenennen; Hinweis, dass gespeicherte Berichtsversionen den alten Namen behalten
  - Beschreibung ändern
  - archivieren bzw. reaktivieren
  - **löschen:** vorher werden die Auswirkungen gezeigt (Anzahl Sensoren, Messungen, Importe, Berichte, aktive Freigaben); bestätigt wird durch Eintippen des Raumnamens
- **Sensoren:**
  - Tabelle: Sensor, Status, Messzeitraum, Messungen, Vollständigkeit, Intervall, Zeitzone, Gerät
  - archivierte Sensoren ein- und ausblenden
  - Sensor anlegen
  - Sensor bearbeiten: Name, Gerät, Zeitzone, erwartetes Messintervall
  - Sensor einem anderen Raum zuordnen (mit Bestätigung)
  - Sensor archivieren bzw. reaktivieren
- **Importprotokolle:**
  - Tabelle: Datum, Datei, Sensor, Status, gelesen, neu, bereits vorhanden, ungültig, Konflikte, Zeitraum, Anzahl Warnungen
  - je Import die Warnungen zum Aufklappen
- **Konflikte (Anzahl):**
  - je Konflikt: Zeitpunkt und je Messgröße vorhandener Wert, neuer Wert, Unterschied
  - Aktionen „Vorhandenen Wert behalten“ und „Neuen Wert übernehmen“
  - alle Konflikte auf einmal auflösen (mit Bestätigung)

---

## 4. Analyse
**Auswahl:**
- Raum und Sensor
- Zeitraum: 7 Tage, 30 Tage, 3 Monate, Jahr, Gesamt oder Frei (dann Datumsbereich); dazu die Anzeige des gewählten Zeitraums
- Messgrößen (Mehrfachauswahl)

**Kennzahlen:**
- Ø Temperatur mit Min und Max
- Ø rel. Feuchte mit Max und Zeitpunkt
- Ø Taupunkt mit kleinstem Abstand
- Zeitabdeckung mit Anzahl Messungen

**8 Bereiche:**
1. **Verlauf:**
   - Zeitreihendiagramm der gewählten Messgrößen mit Zoom-Schnellwahl (24 Std., 7 Tage, 1 Monat, Alles)
   - markierte Feuchtephasen, Schwellenwerte (erhöht/hoch), sichtbare Datenlücken
   - Hinweis, wenn Werte berechnet statt gemessen sind oder die Anzeige vereinfacht ist
2. **Feuchtigkeit:**
   - Zeitanteile in den Bereichen Trocken, Unauffällig, Erhöht, Hoch und Datenlücke
   - Phasen gesamt, kurze Ausschläge vs. anhaltende Phasen, längste Phase mit Höchstwert
   - anhaltende Phasen je Woche, an wie vielen Kalendertagen, Phasen ab „hoch“
   - Liste der Phasen: Beginn, Ende, Dauer, Max, Ø, durch Lücke unterbrochen
   - Phasen als Zeitleiste
   - Tagesverlauf als Stunde × Tag (bis ca. 3 Monate)
   - Tag/Nacht-Vergleich
3. **Taupunkt & Kondensation:**
   - Diagramm mit Temperatur, Taupunkt und Abstand
   - mittlerer und kleinster Abstand, Zeit mit Abstand unter 3 K und unter 1 K
   - Option „Oberflächentemperatur annehmen“ mit Differenz in K; wird deutlich als ANNAHME gekennzeichnet
   - dann zusätzlich: Zeit mit Oberfläche ≤ Taupunkt und mit Oberflächen-rF ≥ 80 %
   - Liste der Phasen mit geringem Abstand
   - Einschränkungshinweis, solange keine Oberflächentemperatur vorhanden ist
4. **VPD & abs. Feuchte:** Verlauf und Kennzahlen, nur beschreibend
5. **Monate & Jahreszeiten:**
   - Messgröße wählen
   - Monatsübersicht als Diagramm und Tabelle, unvollständige Monate gekennzeichnet
   - Tag/Nacht je Monat
   - Jahreszeiten (Frühling, Sommer, Herbst, Winter mit Jahr) mit Datenbasis: ausreichend, Teil der Jahreszeit oder zu wenig Daten
6. **Einschätzung:**
   - Gesamteinschätzung mit Begründung
   - Beobachtungen (Befunde)
   - Lüftungshinweise, getrennt nach Beobachtung, möglicher Erklärung, Empfehlung und Unbekanntem
   - beschreibende Werte
   - Einschränkungen
   - Kriterien der Einschätzung und zugrunde liegende Zahlen (zum Aufklappen)
7. **Datenqualität:**
   - Bewertung (gut/eingeschränkt/unzureichend) mit Gründen
   - Zeitabdeckung, Messvollständigkeit, erwartetes und beobachtetes Intervall, Intervallmix
   - Lücken: Anzahl, Summe, längste; dazu eine Lückenliste
   - Plausibilität: importierte gegenüber berechneten Werten je Messgröße
8. **Veränderung ab Zeitpunkt:**
   - Eingabe: Datum, Uhrzeit, Fenster je Seite (Tage)
   - Ergebnis: vorher/nachher-Tabelle mit Δ und dem Hinweis, dass keine Ursache abgeleitet wird

**Sonderzustände:** keine Messgröße gewählt, kein Sensor, keine Messdaten, keine Messungen im Zeitraum.

---

## 5. Vergleich
**Art des Vergleichs** (6 Möglichkeiten):
1. **Tag / Woche / Monat:** aktueller gegenüber vorherigem Zeitraum (Raum wählen)
2. **Zwei Zeiträume:** Zeitraum A und B frei wählen
3. **Gleich lange Zeiträume:** Zeitraum A und Beginn von B; B ist so lang wie A
4. **Gleicher Monat in Vorjahren:** Monat, Jahr A und Jahr B; Tabelle über alle Jahre
5. **Jahreszeiten:** eine Jahreszeit über mehrere Jahre
6. **Raum / Sensor:** zwei Sensoren im selben Zeitraum

**Ergebnis:**
- Beschriftung von A, B und Δ = B − A
- je Kennzahl: Wert A, Wert B, Δ absolut, Δ % (wo sinnvoll)
- Verlaufsdiagramm beider Zeiträume (Achse „Tag 1 … n“); Messgröße wählbar, Darstellung als Verlauf oder Tagesmittel
- Feuchtephasen und Datenabdeckung: Zeit ≥ erhöht und ≥ hoch, Stunden erhöht, anhaltende Phasen, Phasen je Woche, längste Phase, Zeitabdeckung
- Hinweise „Vergleichbarkeit eingeschränkt“ und „Keine Ursachenaussage“

---

## 6. Berichte & Freigaben
**Neuer Bericht:**
1. Raum wählen; angezeigt werden vorhandener Datenzeitraum und Anzahl Messungen
2. Zeitraum: Letzter Monat, Letzte 3 Monate, Gesamter Datenzeitraum, Kalenderjahr (mit Jahr) oder Frei wählen
3. Inhalte auswählen: Kennzahlen, Zeitverlauf, Luftfeuchtigkeit & Feuchtephasen, Taupunkt, Monatsübersicht, Jahreszeiten, Tag und Nacht, Einschätzung, Datenqualität
4. Optionen:
   - Titel
   - Notiz für Empfänger
   - Sensorname und Geräteinfo anzeigen (ja/nein)
   - Oberflächentemperatur annehmen (Differenz in K)
5. Aktionen: Bericht erstellen (Vorschau, entspricht der öffentlichen Ansicht), Bericht speichern, Entwurf verwerfen

**Vorhandene Berichte** (archivierte lassen sich ein- und ausblenden), je Bericht:
- Titel, Raum, aktuelle Version
- **Hinweis „Datenbasis geändert“**, wenn seither neue Messdaten dazugekommen sind, mit Aktion „Neue Version erstellen“
- **Versionen:**
  - Version auswählen, ansehen, als PDF herunterladen
  - als aktuelle Version festlegen
- **Freigaben:**
  - Tabelle: Bezeichnung, Link-Kennung (letzte Zeichen), erstellt, läuft ab, Zugriffe, letzter Zugriff, Status (aktiv/widerrufen/abgelaufen)
  - Freigabe widerrufen (mit Bestätigung)
- **Neuer Freigabelink:**
  - Bezeichnung (nur intern sichtbar)
  - Gültigkeit: 7 Tage, 30 Tage, 90 Tage, 1 Jahr oder kein Ablaufdatum
  - nach dem Erzeugen: Link einmalig anzeigen, kopieren und ausblenden; dazu der Hinweis, dass jeder mit dem Link den Bericht sehen und weitergeben kann
- Bericht archivieren oder löschen (mit Bestätigung und Hinweis auf die Auswirkungen auf Freigaben)

**Hinweise:** Im lokalen Modus funktionieren Links nur auf diesem Rechner. Fehlt die App-Adresse, wird ein Hinweis angezeigt.

---

## 7. Einstellungen
**Einstellbare Werte:**
- Luftfeuchtigkeit: Trocken unter (%), Erhöht ab (%), Hoch ab (%)
- Feuchtephasen: Anhaltend ab (Minuten), kurze Unterbrechungen überbrücken bis (Minuten)
- Taupunkt: Hinweis bei Abstand unter (K)
- Tag/Nacht: Nacht beginnt um, Nacht endet um (Uhr)
- Datenlücken: Lücke ab einem Vielfachen des Messintervalls, mindestens jedoch (Minuten)
- Zeitzone

**Aktionen:** Einstellungen speichern, Standard wiederherstellen. Hinweis, dass Berichte die Einstellungen ihres Erstellungszeitpunkts behalten.

**Speicherung:** Speichermodus (lokal oder online), Speicherort bzw. Server und angemeldete E-Mail; im Online-Betrieb die Aktion „Abmelden“.

**Dokumentation:** Formeln und Architektur zum Aufklappen.

## 8. Anmelden (nur online)
- E-Mail-Adresse, Passwort, Aktion „Anmelden“
- Fehlermeldungen: falsche Daten, nicht bestätigt, keine Verbindung
- Hinweis, dass die Registrierung nur durch den Administrator erfolgt

---

## 9. Öffentlicher Bericht (per Link, ohne Anmeldung, nur lesen)
Keine Navigation, keine Verwaltungsfunktionen, keine Filter. Erlaubt sind nur: Diagramme
zoomen, Messreihen ein- und ausblenden, zu Abschnitten springen und das PDF herunterladen.
Der Inhalt ist ein gespeicherter Stand und ändert sich nicht durch neue Importe.

**Abschnitte** (nur die beim Erstellen gewählten):
1. **Kopf:** Berichtstitel, Raumname, Zeitraum, erstellt am, Version, Datenabdeckung mit Bewertung, optional Notiz und Sensorinfo
2. **Sprungmarken** zu den Abschnitten
3. **Kurzfazit:** Status, Überschrift, kurzer Text; dazu die Aktion „PDF herunterladen“
4. **Zusammenfassung:** 3–6 Sätze
5. **Kennzahlen:** Ø Temperatur (Min–Max), Ø rel. Feuchte (Min–Max), Anteil der Zeit ≥ erhöht, Ø Abstand zum Taupunkt (kleinster Abstand); Hinweis, was gemessen und was berechnet ist
6. **Einschätzung:** Gesamteinschätzung mit Begründung und den vier Ebenen Messwerte, berechnete Kennzahlen, mögliche Risiken, keine Schadensfeststellung
7. **Zeitverlauf:** Diagramme für Temperatur, rel. Feuchte (mit Min–Max-Spanne und Schwellen) und Taupunkt; Feuchtephasen markiert, Lücken sichtbar
8. **Feuchtephasen:** Anzahl, Dauer, längste Phasen, Zeitleiste
9. **Taupunkt:** Erklärung, Abstands-Kennzahlen, gegebenenfalls Oberflächen-Annahme (gekennzeichnet)
10. **Monate:** Diagramm und Tabelle (Ø Temp., Ø rF, Max rF, Zeit ≥ erhöht, anhaltende Phasen, Abdeckung, vollständig/unvollständig)
11. **Jahreszeiten:** Tabelle mit Datenbasis
12. **Tag & Nacht**
13. **Datenqualität & Einschränkungen:** Einstufung, Messungen, Abdeckung, Intervall, längste Lücken, Einschränkungen
14. **Fazit**
15. **Fußzeile:** „Erstellt mit SwitchBot Climate Dashboard · schreibgeschützte Ansicht“

**Ungültiger, abgelaufener oder widerrufener Link:** Seite „Dieser Bericht ist nicht (mehr)
verfügbar“ mit der Bitte, sich an die Person zu wenden, die den Link geschickt hat.

**PDF-Bericht** (Download) mit denselben Inhalten.
