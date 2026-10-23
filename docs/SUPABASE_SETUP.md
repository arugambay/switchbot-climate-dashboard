# Supabase einrichten (Schritt für Schritt)

Diese Anleitung richtet die Online-Datenbank für das Klima-Dashboard ein. Du brauchst keine
Programmierkenntnisse – nur einen Browser. Dauer: ca. 20 Minuten.

> **Ohne Supabase** läuft die App im lokalen Modus (SQLite-Datei unter `data/`). Das ist für
> Tests gedacht, nicht für dauerhafte Online-Speicherung auf Streamlit Cloud (dort geht die
> Datei bei jedem Neustart verloren).

---

## 1. Konto anlegen

1. <https://supabase.com> öffnen → **Start your project**.
2. Mit GitHub-Konto oder E-Mail registrieren.

## 2. Projekt erstellen

1. **New project** klicken.
2. **Name:** z. B. `klima-dashboard`.
3. **Database Password:** sicheres Passwort erzeugen lassen und im Passwort-Manager
   speichern. (Die App braucht es nicht, aber du brauchst es evtl. später für Backups.)
4. **Region:** **Central EU (Frankfurt)** – Daten bleiben in der EU, kurze Wege.
5. **Plan:** Free.
6. **Create new project** – die Einrichtung dauert 1–2 Minuten.

## 3. Datenbank-Schema einspielen

1. Links im Menü **SQL Editor** öffnen → **New query**.
2. Die Datei `supabase/migrations/20261022000001_initial_schema.sql` aus diesem Projekt in
   einem Texteditor öffnen, **alles** markieren, kopieren und in das Feld einfügen.
3. **Run** klicken. Erwartet: „Success. No rows returned“.
4. Kontrolle: Links **Table Editor** – dort stehen jetzt `rooms`, `sensors`, `measurements`,
   `imports`, `measurement_conflicts`, `reports`, `report_versions`, `shares`,
   `user_settings`. Bei jeder Tabelle muss **RLS enabled** angezeigt werden (kein rotes
   „RLS disabled“/„Unrestricted“).
5. Zusätzlich: **Advisors → Security Advisor** öffnen. Es sollten keine Fehler zu
   „RLS disabled in public“ erscheinen.

Weitere Migrationsdateien (falls später welche dazukommen) immer **in der Reihenfolge ihrer
Nummer** ausführen und jede nur einmal.

## 4. Anmeldung einrichten (nur du selbst)

Die App ist für einen Nutzer gedacht. Jeder Nutzer sieht ausschließlich seine eigenen Daten
(Row Level Security), aber fremde Registrierungen sind trotzdem unerwünscht.

1. **Authentication → Sign In / Providers**: **Email** muss aktiviert sein.
2. **Deinen Nutzer anlegen:** **Authentication → Users → Add user → Create new user**.
   E-Mail und ein starkes Passwort eingeben, **Auto Confirm User** anhaken → **Create user**.
3. **Registrierung abschalten:** **Authentication → Sign In / Providers** →
   **Allow new users to sign up** **deaktivieren** → **Save**.
   Danach kann sich niemand mehr selbst ein Konto anlegen; neue Nutzer nur noch über Schritt 2.

## 5. URL und öffentlichen Schlüssel finden

**Project Settings** (Zahnrad) → **Data API** bzw. **API Keys**:

| Wert | Wo | Verwendung |
|---|---|---|
| **Project URL** | `https://<id>.supabase.co` | `SUPABASE_URL` |
| **anon / public** (bzw. **publishable**, beginnt mit `sb_publishable_`) | API Keys | `SUPABASE_ANON_KEY` |

> ⚠️ **Niemals** den Schlüssel **`service_role`** bzw. **secret** (`sb_secret_…`) verwenden!
> Er umgeht alle Zugriffsregeln. Die App braucht ihn nicht und **verweigert den Start**, wenn
> er als `SUPABASE_SERVICE_ROLE_KEY` gesetzt oder als `SUPABASE_ANON_KEY` eingetragen ist.
> Der anon-Schlüssel darf öffentlich sein – der Schutz kommt aus den RLS-Regeln.

## 6. Secrets in Streamlit Community Cloud eintragen

App auf <https://share.streamlit.io> → **⋮ → Settings → Secrets**:

```toml
SUPABASE_URL = "https://abcdefgh.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOi…"        # anon/publishable – NICHT service_role
APP_BASE_URL = "https://mein-klima.streamlit.app"
DEFAULT_TIMEZONE = "Europe/Berlin"
```

`APP_BASE_URL` wird für Freigabelinks (`…/?r=<token>`) benötigt. **Save** → die App startet neu
und zeigt die Anmeldeseite. Lokal dieselben Werte in eine Datei `.env` schreiben (Vorlage
`.env.example`; `.env` wird nicht ins Git-Repository übernommen).

## 7. Grenzen des kostenlosen Tarifs (Free Tier)

| Grenze | Bedeutung für dich |
|---|---|
| **500 MB Datenbank** | siehe Speicherschätzung unten |
| **Projekt pausiert nach 7 Tagen ohne Aktivität** | siehe Warnung |
| 2 aktive Free-Projekte pro Konto | genügt |

> ⚠️ **Pausiertes Projekt = geteilte Links funktionieren nicht!**
> Wird die Datenbank eine Woche lang nicht benutzt, pausiert Supabase das Projekt. Dann sind
> weder die App noch **geteilte Berichtslinks** erreichbar („Keine Verbindung zur Datenbank“),
> bis du das Projekt im Supabase-Dashboard mit **Restore project** reaktivierst (dauert einige
> Minuten; Daten bleiben erhalten). Wenn du einem Vermieter/Gutachter einen Link schickst,
> öffne die App regelmäßig selbst oder plane einen Upgrade auf einen bezahlten Tarif ein.

### Speicherschätzung

Eine Messung belegt inklusive Primärschlüssel-Index und Import-Index **ca. 150–200 Bytes**.
**10.000 Messungen ≈ 2 MB.**

| Messintervall | Messungen pro Sensor und Jahr | Speicher pro Sensor und Jahr |
|---|---|---|
| 30 min | 17.520 | ≈ 3,5 MB |
| 10 min | 52.560 | ≈ 10 MB |
| 5 min | 105.120 | ≈ 21 MB |
| 1 min | 525.600 | ≈ 105 MB |

Mehrere Sensoren über Jahre sind bei üblichen Intervallen (≥ 5 min) unkritisch. Nur bei
Minuten-Intervallen wird die 500-MB-Grenze nach einigen Sensor-Jahren relevant. Berichte
(Snapshots) brauchen je Version typischerweise 50–300 KB. Aktueller Verbrauch:
**Project Settings → Usage** bzw. **Reports → Database**.

## 8. Fehlerdiagnose

| Meldung / Symptom | Ursache | Lösung |
|---|---|---|
| App startet mit „SUPABASE_SERVICE_ROLE_KEY ist gesetzt“ oder „geheimer Service-Role-Schlüssel“ | falscher Schlüssel | in den Secrets den **anon/publishable**-Schlüssel eintragen, Service-Key entfernen |
| App zeigt lokalen Modus statt Anmeldung | `SUPABASE_URL` oder `SUPABASE_ANON_KEY` fehlt/leer | Secrets prüfen (Schreibweise, Anführungszeichen) |
| „E-Mail-Adresse oder Passwort ist falsch“ | Tippfehler oder Nutzer fehlt | Nutzer unter Authentication → Users prüfen, ggf. Passwort zurücksetzen |
| „Die E-Mail-Adresse ist noch nicht bestätigt“ | Nutzer ohne Auto Confirm angelegt | Nutzer löschen und mit **Auto Confirm** neu anlegen |
| „Keine Verbindung zur Datenbank (Supabase)…“ | Projekt pausiert oder kein Internet | Supabase-Dashboard → **Restore project** |
| „Die Anmeldung ist abgelaufen“ | Sitzung (JWT) abgelaufen | erneut anmelden |
| „Keine Berechtigung für diese Aktion“ | RLS verweigert (fremde Daten, falsche Sitzung) oder Migration unvollständig | abmelden/anmelden; Migration vollständig ausgeführt? |
| SQL-Editor: `relation "public.rooms" already exists` | Migration zum zweiten Mal ausgeführt | nichts tun – sie ist bereits eingespielt |
| Freigabelink zeigt „nicht verfügbar“ | Link widerrufen, abgelaufen, Bericht archiviert/gelöscht, oder Projekt pausiert | in der App unter Freigaben prüfen |

## Was die Datenbank absichert (Kurzfassung)

- **Row Level Security auf allen Tabellen:** Jede Zeile hat `owner_id`; lesen/schreiben/löschen
  darf nur der angemeldete Besitzer. Nicht angemeldete Besucher (Rolle `anon`) haben keinerlei
  Tabellenzugriff.
- **Freigabelinks:** In der Datenbank steht nur der SHA-256-Hash des Tokens. Öffentlich
  erreichbar ist allein die Funktion `get_shared_report(token)`; sie liefert ausschließlich den
  gespeicherten Berichts-Snapshot (Inhalt, Version, Erstellungs- und Ablaufdatum) – keine IDs,
  keine Rohdaten – und nur, wenn der Link nicht widerrufen/abgelaufen und der Bericht nicht
  archiviert ist.
- **Berichtsversionen** sind unveränderlich (Datenbank-Trigger).
- Hinweis: Diese Regeln wurden im Projekt **statisch** (Textprüfung der SQL-Datei) und mit
  simulierten Clients getestet, nicht automatisch gegen eine echte Supabase-Instanz. Nach dem
  Einrichten lohnt ein kurzer Selbsttest: mit einem zweiten Testnutzer anmelden und prüfen, dass
  keine fremden Räume sichtbar sind.
