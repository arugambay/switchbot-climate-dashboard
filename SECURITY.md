# Sicherheit

## Sicherheitslücke melden

Bitte melde Sicherheitsprobleme **nicht** als öffentliches Issue, sondern über
[GitHub Security Advisories](https://github.com/arugambay/switchbot-climate-dashboard/security/advisories/new)
("Report a vulnerability").

## Grundsätze dieses Projekts

- Der Supabase-**Service-Role-Schlüssel** wird von der App nicht benötigt und darf nirgends
  (Repository, Streamlit-Secrets, Logs) hinterlegt werden. Die App verweigert den Start, wenn er
  in der Konfiguration gefunden wird.
- Datenzugriff wird in der Datenbank über **Row Level Security** pro Nutzer geschützt, nicht nur
  über ausgeblendete Bedienelemente.
- **Freigabelinks** enthalten einen zufälligen Token mit 256 Bit Entropie. Gespeichert wird nur
  dessen SHA-256-Hash. Links sind widerrufbar und können ein Ablaufdatum haben.
- Wer einen gültigen Freigabelink besitzt, kann den Bericht sehen und den Link weitergeben.
  Teile Links daher nur mit Personen, die den Bericht sehen dürfen.
- Echte Messdaten (CSV-Exporte), lokale Datenbanken und `.env`/`secrets.toml` sind per
  `.gitignore` ausgeschlossen; ein automatischer Test prüft, dass solche Dateien nicht im
  Repository landen.
