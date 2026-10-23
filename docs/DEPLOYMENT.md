# Veröffentlichen auf Streamlit Community Cloud

Damit ist die App dauerhaft online erreichbar, auch wenn dein PC ausgeschaltet ist.
Vorher bitte die Datenbank einrichten: [SUPABASE_SETUP.md](SUPABASE_SETUP.md). Ohne
Supabase gehen die Daten auf Streamlit Cloud bei jedem Neustart verloren.

## 1. Code auf GitHub

Der aktuelle Stand muss im Repository `arugambay/switchbot-climate-dashboard` auf dem Branch
`main` liegen.

## 2. App anlegen

1. Öffne <https://share.streamlit.io> und melde dich mit deinem GitHub-Konto an.
2. Klicke auf **Create app** → **Deploy a public app from GitHub**.
3. Trage ein:
   - **Repository:** `arugambay/switchbot-climate-dashboard`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** einen Namen nach Wahl, z. B. `mein-klima` → `https://mein-klima.streamlit.app`
4. Unter **Advanced settings** → **Python version** wählst du **3.12**.
5. Ebenfalls unter **Advanced settings** → **Secrets** fügst du die Werte aus
   SUPABASE_SETUP.md, Schritt 6, ein. Bei `APP_BASE_URL` trägst du genau die App-Adresse
   aus Punkt 3 ein, ohne Schrägstrich am Ende.
6. Klicke auf **Deploy**. Der erste Start dauert einige Minuten, weil die Abhängigkeiten
   installiert werden.

Danach erscheint die Anmeldeseite. Melde dich mit dem Nutzer an, den du in Supabase angelegt
hast.

## 3. Updates

Jeder neue Stand auf `main` wird automatisch neu veröffentlicht.

## 4. Grenzen des kostenlosen Tarifs

- Apps ohne Besucher schlafen nach einiger Zeit ein. Der nächste Aufruf weckt sie wieder;
  das dauert etwa 30 Sekunden. Das gilt auch für Empfänger eines Freigabelinks.
- Der Arbeitsspeicher ist begrenzt (rund 1 GB). Für mehrere Jahre Messdaten mehrerer Sensoren
  reicht das, weil die App immer nur den gewählten Zeitraum lädt.
- Die App ist öffentlich erreichbar. Das ist gewollt: Ohne Anmeldung sieht man nur die
  Anmeldeseite bzw. mit gültigem Link einen geteilten Bericht.

## 5. Fehlerdiagnose

| Symptom | Lösung |
|---|---|
| „Error installing requirements“ | Python 3.12 gewählt? Logs unter **Manage app** ansehen |
| Hinweis „Lokaler Entwicklungsmodus“ online | Secrets fehlen oder sind falsch geschrieben |
| Freigabelinks zeigen auf `localhost` | `APP_BASE_URL` in den Secrets korrigieren |
| „Keine Verbindung zur Datenbank“ | Supabase-Projekt pausiert → im Supabase-Dashboard **Restore project** |
