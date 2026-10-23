# Formeln, Annahmen und Einschätzungslogik

Dieses Dokument erklärt, wie das Dashboard aus den Messwerten Kennzahlen und Hinweise berechnet.
Teil A ist für Laien geschrieben, Teil B enthält die technischen Details (Formeln, Einheiten,
Grenzen, Quellen). Der Code dazu liegt in `climate_dashboard/analysis/`.

> **Wichtig vorab:** Ein Raumklima-Sensor misst die *Luft* an *einer Stelle* im Raum. Ob an einer
> Wand Tauwasser entsteht oder Schimmel wächst, hängt von der Temperatur der Wandoberfläche, dem
> Material und der Dauer ab. Das Dashboard kann deshalb **keinen Schimmel und keine Kondensation
> feststellen oder ausschließen**. Es beschreibt Bedingungen, die dafür günstig oder ungünstig sind.

---

## Teil A – Verständliche Erklärung

### 1. Was wird gemessen, was wird berechnet?

| Größe | Bedeutung | Herkunft |
|---|---|---|
| Temperatur (°C) | Lufttemperatur am Sensor | gemessen |
| Relative Luftfeuchtigkeit (% rF) | Wie viel Prozent der Wasserdampfmenge, die die Luft bei dieser Temperatur höchstens aufnehmen kann, tatsächlich enthalten ist | gemessen |
| Taupunkt (°C) | Temperatur, auf die man die Luft abkühlen müsste, damit Wasser kondensiert | aus der Datei oder berechnet |
| Absolute Luftfeuchtigkeit (g/m³) | Gramm Wasserdampf je Kubikmeter Luft | aus der Datei oder berechnet |
| Dampfdruckdefizit, VPD (kPa) | Wie „durstig“ die Luft ist – wie weit sie von der Sättigung entfernt ist | aus der Datei oder berechnet |

Fehlen Taupunkt, absolute Feuchte oder VPD in der Datei, werden sie aus Temperatur und relativer
Feuchte berechnet und als **berechnet** gekennzeichnet. Die Originalwerte der Datei werden nie
verändert.

### 2. Warum „zeitgewichtet“?

Sensoren messen nicht immer im gleichen Takt. In echten Exporten wechseln sich Abstände von 1, 10,
30, 60 oder 120 Minuten ab. Würde man einfach alle Messwerte mitteln, zählte eine Stunde mit
60 Messungen sechzigmal so viel wie eine Stunde mit einer Messung.

Deshalb gilt: **Jede Messung steht für die Zeit bis zur nächsten Messung.** Mittelwerte, Anteile
und Dauern werden mit dieser Zeit gewichtet. Beispiel: 30 Minuten mit 40 % und danach 10 Minuten
mit 80 % (in zehn Minutenmessungen) ergeben zeitgewichtet 50 %, gezählt wären es 76 %.

### 3. Datenlücken

Ist der Abstand zwischen zwei Messungen deutlich größer als üblich, liegt eine **Datenlücke** vor.
Die Grenze ist das Dreifache des erwarteten Messintervalls, mindestens aber 60 Minuten
(einstellbar). Bei 30-Minuten-Daten ist das also 90 Minuten.

- Über eine Lücke wird **nichts erfunden** – kein Auffüllen, keine Interpolation.
- Die Messung vor der Lücke zählt nur für ein übliches Intervall, der Rest ist „unbekannt“.
- Lückenzeit zählt **weder als auffällig noch als unauffällig**. Anteile („42 % der Zeit erhöht“)
  beziehen sich immer auf die Zeit *mit* Messwerten.

### 4. Zwei Abdeckungsmaße

- **Zeitabdeckung:** Welcher Anteil des Zeitraums ist durch Messungen abgedeckt (nicht in Lücken)?
- **Messvollständigkeit:** Wie viele Messungen liegen vor, verglichen mit der Zahl, die beim
  erwarteten Intervall zu erwarten wäre (höchstens 100 %)?

Die Datenqualität eines Zeitraums wird so eingestuft:

| Einstufung | Bedingung |
|---|---|
| gut | Zeitabdeckung ≥ 90 % und keine einzelne Lücke über 25 % des Zeitraums |
| eingeschränkt | Zeitabdeckung 60–90 % oder eine Lücke über 25 % des Zeitraums |
| unzureichend | Zeitabdeckung unter 60 % oder weniger als 2 Messungen |

Lücken am Anfang oder Ende des gewählten Zeitraums zählen mit.

### 5. Feuchtebereiche

Voreinstellung (änderbar): **trocken** unter 35 %, **normal** 35–60 %, **erhöht** 60–70 %,
**hoch** ab 70 %. Diese Werte sind übliche Orientierungswerte für Wohnräume, keine Grenzwerte.

### 6. Feuchtephasen

Eine **Phase** ist eine zusammenhängende Zeit, in der die relative Feuchte bei oder über der
Schwelle (Standard 60 %) lag.

- Kurze Unterbrechungen unter 30 Minuten werden überbrückt (aus „62 %, 59 %, 63 %“ werden nicht
  zwei Phasen).
- Hält eine Phase mindestens **120 Minuten** an, heißt sie **anhaltend**, sonst
  **kurzer Ausschlag**. Kurze Spitzen entstehen z. B. beim Duschen oder Kochen; anhaltende Phasen
  sind für Bauteile bedeutsamer, weil Oberflächen Zeit brauchen, um feucht zu werden.
- Endet eine Phase an einer Datenlücke, wird sie als **„durch Lücke unterbrochen“** markiert – ihre
  wahre Dauer ist dann unbekannt (eher länger).

### 7. Taupunktabstand und Kondensation

Der **Abstand zwischen Lufttemperatur und Taupunkt** (in Kelvin) sagt: *So viel kälter als die
Raumluft müsste eine Oberfläche sein, damit dort rechnerisch Tauwasser entsteht.* Ein Abstand von
3 K bedeutet: Eine Fensterscheibe, die 3 °C kälter als die Raumluft ist, wäre bereits am Taupunkt.

Ohne gemessene Oberflächentemperatur gibt das Dashboard deshalb **keine Kondensationsaussage** ab,
sondern nur den Hinweis: *„Eine zuverlässige Kondensationsbewertung ist ohne
Oberflächentemperatur nur eingeschränkt möglich.“*

Optional kann man eine **Annahme** treffen („Die Außenwandecke ist etwa 5 K kälter als die
Raumluft“). Dann rechnet das Dashboard für diese gedachte Oberfläche:

- wie lange ihre Temperatur am oder unter dem Taupunkt lag (rechnerisch mögliche Kondensation),
- wie lange die relative Feuchte *an der Oberfläche* 80 % oder mehr betrug.

Alle Ergebnisse aus einer Annahme werden ausdrücklich als **ANNAHME** gekennzeichnet. Eine
gemessene Oberflächentemperatur hat Vorrang.

**Warum 80 % an der Oberfläche?** In der Bauphysik gilt eine relative Feuchte von etwa 80 % an der
Bauteiloberfläche als Orientierungswert, ab dem Schimmelpilzwachstum möglich wird, wenn sie über
längere Zeit anhält (DIN 4108-2 legt darauf den Mindestwärmeschutz aus; DIN EN ISO 13788 nutzt
denselben Wert als Monatsmittel). Viele Schimmelpilze können bei günstigen Bedingungen schon ab ca.
70 % wachsen (Sedlbauer 2001). Der Wert ist also eine **grobe Orientierung, keine scharfe Grenze**.

### 8. Wie entsteht die Einschätzung?

Die Einschätzung ist **regelbasiert und transparent** – es gibt bewusst *keine* Punktzahl, die
eine wissenschaftlich geprüfte Genauigkeit vortäuschen würde. Jeder Hinweis nennt die Zahlen, auf
denen er beruht. Hinweise haben drei Stufen: **ok**, **Hinweis**, **auffällig**.

| Kategorie | Hinweis, wenn … | auffällig, wenn … |
|---|---|---|
| Feuchte | ≥ 10 % der Zeit bei ≥ 60 % rF | ≥ 30 % der Zeit bei ≥ 60 % oder ≥ 10 % der Zeit bei ≥ 70 % |
| Dauer | mindestens eine anhaltende Phase (≥ 120 min, ≥ 60 %) | ≥ 3 anhaltende Phasen bei ≥ 70 % |
| Taupunkt | ≥ 5 % der Zeit Taupunktabstand < 3 K | ≥ 20 % der Zeit |
| Kondensation | ohne Oberflächenwert: Einschränkungshinweis (nur wenn Feuchte/Taupunkt Anlass geben); mit Oberflächenwert: Oberflächen-rF ≥ 80 % | Oberfläche insgesamt ≥ 1 h am/unter dem Taupunkt (nur mit Messung oder Annahme) |
| Trocken | ≥ 20 % der Zeit unter 35 % rF | – |
| Temperatur | ≥ 20 % der Zeit unter 16 °C (kühle Räume → kalte Oberflächen) | – |
| Datenqualität | Einstufung „eingeschränkt“ oder abgeleitete Werte der Datei weichen ab | Einstufung „unzureichend“ |

Der **Gesamtstatus** hat vier Stufen (die höchste zutreffende gilt):

1. **Möglicherweise schimmelbegünstigende Bedingungen** – nur bei *anhaltend hoher Feuchte über
   längere Dauer*: an mindestens 3 Tagen innerhalb von 7 aufeinanderfolgenden Tagen jeweils
   mindestens 6 Stunden in anhaltenden Phasen mit ≥ 70 % rF in der Raumluft; oder – nur mit
   Oberflächenwert – an mindestens 3 Tagen innerhalb einer Woche jeweils ≥ 12 Stunden mit ≥ 80 %
   rechnerischer Oberflächenfeuchte.
2. **Mögliche Kondensationsbedingungen** – nur mit Oberflächentemperatur (gemessen oder
   angenommen): Oberfläche insgesamt mindestens 1 Stunde rechnerisch am oder unter dem Taupunkt.
3. **Möglicherweise kritische Feuchtebedingungen** – eine der „auffällig“-Bedingungen für Feuchte,
   Dauer oder Taupunkt ist erfüllt.
4. **Gemessene Bedingungen unauffällig** – keine der obigen Bedingungen.

Zusätzlich wird der Status als **unsicher** markiert, wenn die Datenqualität nicht „gut“ ist. Bei
weniger als zwei Messungen lautet der Status „Keine Einschätzung möglich“.

**Immer genannte Einschränkungen:** Schimmelbefall kann aus Raumluftmessungen weder festgestellt
noch ausgeschlossen werden. Ohne Außenmessung ist keine Aussage darüber möglich, ob Lüften die
Feuchte gesenkt hätte. Die Messung gilt nur für den Ort des Sensors.

**Lüftungshinweise** erscheinen nur, wenn die Daten ein Muster zeigen, und trennen immer
*Beobachtung*, *mögliche Erklärung*, *Empfehlung* und *Unbekanntes*:

- erhöhte Feuchte vor allem nachts (Nachtanteil ≥ 20 % und ≥ 15 Prozentpunkte über dem Tag),
- viele kurze Spitzen (≥ 2 kurze Ausschläge je Woche, mehr kurze als anhaltende),
- wiederkehrende anhaltende Phasen (≥ 1 je Woche).

### 9. Zeiträume, Monate, Jahreszeiten, Vergleiche

- Tage, Wochen (Montag–Sonntag), Monate und Jahreszeiten werden in **Ortszeit** gebildet. Tage der
  Zeitumstellung haben 23 bzw. 25 Stunden; die Abdeckung bezieht sich auf die tatsächliche Länge.
- Ein **Monat gilt als vollständig**, wenn er ganz im Datenzeitraum liegt und die Zeitabdeckung
  mindestens 80 % beträgt.
- **Jahreszeiten** sind meteorologisch: Frühling März–Mai, Sommer Juni–August, Herbst
  September–November, Winter Dezember–Februar (z. B. „Winter 2025/26“). Sie gelten als
  **ausreichend** belegt, wenn mindestens 80 % der *ganzen* Jahreszeit abgedeckt sind.
- **Vergleiche** zeigen Wert A, Wert B und die Differenz B − A.
  - Temperaturen und Taupunkte: nur die Differenz in **Kelvin**. Eine Prozentangabe wäre bei °C
    sinnlos, weil 0 °C kein natürlicher Nullpunkt ist (von 10 °C auf 20 °C ist nicht
    „doppelt so warm“).
  - Prozentgrößen (rF, Zeitanteile): Differenz in **Prozentpunkten**, keine relative Änderung.
  - Relative Änderung in % nur bei echten Mengen (absolute Feuchte, VPD, Stunden, Anzahlen).
  - Warnhinweise bei ungleicher Datenqualität, unterschiedlichen Messintervallen oder
    unterschiedlich langen Zeiträumen; dazu immer: Unterschiede können auch von Wetter, Jahreszeit
    und Nutzung kommen.
- **Vorher/Nachher** (z. B. nach einer Maßnahme): gleich lange Fenster (Standard 7 Tage) vor und
  nach dem Zeitpunkt; die Texte beschreiben nur Unterschiede und behaupten keine Ursache.

---

## Teil B – Technischer Anhang

### B1. Magnus-Formel (über Wasser)

Koeffizienten nach Alduchov & Eskridge (1996): a = 17,62; b = 243,12 °C; e₀ = 0,6112 kPa.

```
Sättigungsdampfdruck   e_s(T) = 0,6112 kPa · exp(a·T / (b + T))          T in °C
Dampfdruck             e      = rF/100 · e_s(T)
Taupunkt               γ      = ln(rF/100) + a·T / (b + T)
                       T_d    = b·γ / (a − γ)
Rel. Feuchte aus T_d   rF(T)  = 100 · e_s(T_d) / e_s(T)                  (> 100 % = unter Taupunkt)
Absolute Feuchte       ρ_w    = 216,7 · e[hPa] / (273,15 + T)          g/m³
                       (216,7 g·K/(m³·hPa) = 100 Pa/hPa · 1000 g/kg / R_v, R_v = 461,5 J/(kg·K))
Dampfdruckdefizit      VPD    = e_s(T) · (1 − rF/100)                  kPa
Taupunktabstand        ΔT_d   = T − T_d                                K
```

Referenzwerte (Tests): T = 20 °C, rF = 50 % → e_s = 2,333 kPa, T_d = 9,26 °C, ρ_w = 8,62 g/m³,
VPD = 1,17 kPa. T = 19,2 °C, rF = 57 % → T_d = 10,5 °C.

**Gültigkeit und Grenzen:** etwa −45 °C bis 60 °C (relativer Fehler von e_s < 0,4 %). Außerhalb
sowie bei rF ≤ 0 % oder rF > 100 % liefern die Funktionen NaN (kein Wert). Unter 0 °C wird bewusst
über Wasser gerechnet (wie bei üblichen Raumklimasensoren); über Eis wären andere Koeffizienten
nötig. Der Luftdruck geht nicht ein (Abweichung durch Höhenlage vernachlässigbar für diese Zwecke).

### B2. Plausibilitätsprüfung (Datei vs. Berechnung)

Die Werte der Datei werden mit der Berechnung aus T und rF verglichen. Toleranzen:
Taupunkt 0,5 K, VPD 0,05 kPa, absolute Feuchte 0,3 g/m³. Mehr als 1 % Überschreitungen gelten als
auffällig (andere Formel, Einheitenfehler, vertauschte Spalten).

Begründung über die Rundung: T ist auf 0,1 °C (± 0,05), rF auf 1 % (± 0,5) gerundet, die
Zielwerte selbst auf 0,1 K bzw. 0,01. Die Empfindlichkeiten (berechnet):

| Größe | Einfluss rF ± 0,5 % | Einfluss T ± 0,05 °C | Rundung Zielwert | Summe (ungünstig) |
|---|---|---|---|---|
| Taupunkt | ± 0,15 K (50 %) … ± 0,24 K (30 %) | ± 0,05 K | ± 0,05 K | ≈ 0,35 K |
| VPD | ± 0,016 kPa (25 °C) | ± 0,005 kPa | ± 0,005 kPa | ≈ 0,03 kPa |
| Abs. Feuchte | ± 0,12 g/m³ (25 °C) | ± 0,05 g/m³ | ± 0,005 g/m³ | ≈ 0,17 g/m³ |

Die Toleranzen liegen mit Sicherheitsabstand darüber, aber deutlich unter typischen
Formel-/Einheitenfehlern. Validierung gegen einen echten SwitchBot-Export: max. |Δ| Taupunkt
0,16 K, VPD 0,03 kPa, absolute Feuchte 0,12 g/m³ – keine Überschreitung.

### B3. Zeitgewichte und Lücken

Für aufsteigend sortierte Zeitpunkte t₁ … tₙ, Abstände Δᵢ = tᵢ₊₁ − tᵢ, Lückengrenze
G = max(gap_factor · I_erwartet, min_gap_minutes):

```
wᵢ = Δᵢ                     wenn Δᵢ ≤ G
wᵢ = min(I_nominal, G)      wenn Δᵢ > G (Lücke) oder i = n (letzte Messung)
wᵢ ≤ Periodenende − tᵢ
```

I_nominal = erwartetes Intervall (Vorgabe) bzw. Median der regulären Abstände. Das erwartete
Intervall wird ohne Vorgabe als Median aller positiven Abstände erkannt (ab 1 min auf ganze Minuten
gerundet). Gewichte werden auf dem vollständigen Datensatz berechnet und an Periodengrenzen
gekappt. Zeit zwischen Periodenbeginn und erster Messung gilt als nicht abgedeckt.

- Zeitabdeckung = Σwᵢ / Periodenlänge.
- Messvollständigkeit = n / (Periodenlänge / I_erwartet), höchstens 100 %.
- Gelistete Lücken: Abstände > G (Dauer = voller Abstand), dazu Rand-Lücken
  Periodenbeginn → erste Messung und letzte Messung → Periodenende, wenn > G.
- Fehlt bei einer Messung ein bestimmter Wert (z. B. rF), wird sie für diese Größe wie eine
  fehlende Messung behandelt (Gewichte für diese Größe neu berechnet).

Kennzahlen: zeitgewichtetes Mittel Σwᵢxᵢ/Σwᵢ; zeitgewichtete Standardabweichung; gewichtete
Quantile (p05, Median, p95) als kleinster Wert, bei dem der kumulierte Zeitanteil ≥ q ist;
Min/Max mit Zeitpunkt (ungewichtet). Ohne verwertbare Gewichte (eine einzelne Messung) wird
ungewichtet gerechnet und `time_weighted = false` gesetzt.

### B4. Phasen

Zeilen mit Wert ≥ Schwelle bilden Segmente; ein Segment endet auch an jeder Lücke. Unterhalb-
Segmente mit Gesamtdauer < `episode_merge_gap_minutes` zwischen zwei Oberhalb-Segmenten werden
überbrückt, sofern keine Lücke dazwischen liegt. Beginn = erster Zeitpunkt, Ende = letzter
Zeitpunkt + dessen Gewicht. `duration_s` = Ende − Beginn (inkl. Überbrückung), `above_s` = Zeit
tatsächlich über der Schwelle. `is_sustained`: duration ≥ `min_episode_minutes`. Kennwerte je
Phase: max/mittlere rF (zeitgewichtet), mittlere Temperatur, minimaler Taupunktabstand.
Phasen je Woche beziehen sich auf die abgedeckte Zeit. Tage mit anhaltender Phase = Kalendertage
(Ortszeit), die eine anhaltende Phase berührt.

Tag/Nacht: Zuordnung nach dem Zeitstempel der Messung (Nacht = `night_start_hour` bis
`night_end_hour`, Ortszeit); das Gewicht einer Messung wird nicht an der Grenze geteilt (Fehler
höchstens ein Messintervall je Übergang).

### B5. Aggregation

Fenster `h` (UTC-Stunden = Ortsstunden, da Europe/Berlin ganzstündige Offsets hat), `D`, `W`
(ISO-Woche ab Montag), `MS` (Kalendermonat) in Ortszeit. Fensterlänge = tatsächliche Dauer
(23/25 h an Umstellungstagen). Jede Messung zählt zum Fenster ihres Zeitstempels; Abdeckung =
Σw / Fensterlänge, auf 100 % begrenzt. Fenster ohne Messung bleiben leer (NaN).

### B6. Oberflächenbetrachtung

```
T_s       = gemessene Oberflächentemperatur   oder   T − surface_offset_k (ANNAHME)
rF_s      = min(100, 100 · e_s(T_d) / e_s(T_s))
Kondensation rechnerisch möglich, wenn T_s ≤ T_d
```

Gemessene Oberflächenwerte werden dem nächstgelegenen Messzeitpunkt (Toleranz = nominales
Intervall) zugeordnet. Kennzahlen: Stunden/Anteil T_s ≤ T_d, Stunden/Anteil rF_s ≥ 80 %, Tage mit
≥ 12 h rF_s ≥ 80 % (und deren größte Zahl innerhalb von 7 Tagen).

### B7. Kriterien der Einschätzung (Konstanten in `assessment.py`)

| Konstante | Wert |
|---|---|
| ELEVATED_SHARE_NOTE_PCT / _STRONG_PCT | 10 % / 30 % der abgedeckten Zeit ≥ `humidity_elevated` |
| HIGH_SHARE_STRONG_PCT | 10 % der Zeit ≥ `humidity_high` |
| HIGH_SUSTAINED_STRONG_COUNT | 3 anhaltende Phasen ≥ `humidity_high` |
| SPREAD_SHARE_NOTE_PCT / _STRONG_PCT | 5 % / 20 % der Zeit Taupunktabstand < `dew_point_spread_warn` |
| DRY_SHARE_NOTE_PCT | 20 % der Zeit < `humidity_low` |
| COOL_ROOM_C / COOL_SHARE_NOTE_PCT | 16 °C / 20 % der Zeit |
| CONDENSATION_MIN_HOURS | 1 h Oberfläche ≤ Taupunkt |
| MOULD_MIN_DAYS / MOULD_WINDOW_DAYS / MOULD_DAILY_HOURS_ROOM | 3 Tage in 7 Tagen, je ≥ 6 h anhaltend ≥ `humidity_high` |
| Oberfläche (condensation.py) | rF_s ≥ 80 %, ≥ 12 h je Tag |

Die Raumluft-Schwelle von 70 % für das Schimmel-Kriterium ist so gewählt, dass sie bei üblichen
Raumtemperaturen (≈ 20 °C) einer Oberflächenfeuchte von 80 % an einer nur etwa 2 K kälteren
Oberfläche entspricht – also schon an wenig gekühlten Bauteilen. Die Kriterien sind
**Orientierungswerte**, nicht validiert gegen tatsächlichen Schimmelbefall.

### B8. Bekannte Grenzen

- Punktmessung: Raumecken, Außenwände und Bereiche hinter Möbeln können deutlich kälter/feuchter sein.
- Sensorgenauigkeit typischer Raumklimasensoren: etwa ± 0,2–0,5 °C und ± 2–5 % rF; Rundung der
  Exporte (0,1 °C, 1 %).
- Fehlende/ausgedünnte Messungen in Exporten: Lange Abstände werden konservativ als Lücke
  behandelt; dadurch können Phasen als „durch Lücke unterbrochen“ erscheinen.
- Keine Außenmessung: Lüftungswirkung kann nicht bewertet werden.
- Keine Material- oder Dauerdynamik (z. B. Feuchtespeicherung in Wänden).

### B9. Quellen

- Alduchov, O. A.; Eskridge, R. E. (1996): *Improved Magnus Form Approximation of Saturation Vapor
  Pressure.* Journal of Applied Meteorology 35, 601–609.
- DIN 4108-2: *Wärmeschutz und Energie-Einsparung in Gebäuden – Teil 2: Mindestanforderungen an den
  Wärmeschutz* (Kriterium zur Vermeidung von Schimmelpilzbildung: Oberflächenfeuchte 80 %,
  Temperaturfaktor f_Rsi ≥ 0,70).
- DIN EN ISO 13788: *Wärme- und feuchtetechnisches Verhalten von Bauteilen – Raumseitige
  Oberflächentemperatur zur Vermeidung kritischer Oberflächenfeuchte und Tauwasserbildung im
  Bauteilinneren.*
- Sedlbauer, K. (2001): *Vorhersage von Schimmelpilzbildung auf und in Bauteilen.* Dissertation,
  Universität Stuttgart / Fraunhofer IBP (Isoplethenmodell).
- Umweltbundesamt (2017): *Leitfaden zur Vorbeugung, Erfassung und Sanierung von Schimmelbefall in
  Gebäuden.*
