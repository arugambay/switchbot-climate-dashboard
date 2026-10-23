# Mitwirken

Danke für dein Interesse!

1. Repository forken und klonen.
2. Python 3.12 installieren, dann:
   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
   # bzw. .venv/bin/python -m pip install -r requirements-dev.txt
   ```
3. Tests und Linter ausführen: `pytest` und `ruff check .`
4. Änderungen in einem eigenen Branch, Pull Request mit kurzer Beschreibung.

Regeln:
- Tests verwenden ausschließlich **synthetische Daten** (`tests/synthetic.py`). Keine echten
  Messdaten committen.
- Die Benutzeroberfläche ist deutsch; Fachtexte sachlich und vorsichtig formulieren (keine
  Aussagen über tatsächlichen Schimmelbefall aus Raumluftmessungen).
- Schnittstellen zwischen Modulen sind in `docs/ARCHITECTURE.md` beschrieben.
