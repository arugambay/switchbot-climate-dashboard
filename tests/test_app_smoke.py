"""Smoke-Tests der Streamlit-App mit ``streamlit.testing.v1.AppTest``."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


@pytest.fixture(autouse=True)
def local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "smoke.sqlite"))
    for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.chdir(ROOT)


def _html(at: AppTest) -> str:
    return "\n".join(el.proto.body for el in at.get("html"))


def test_app_starts_in_local_mode():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    labels = [el.proto.label for el in at.get("page_link")]
    for expected in ("Übersicht", "Import", "Analyse", "Vergleich", "Räume & Sensoren", "Berichte & Freigaben",
                     "Einstellungen", "Styleguide"):  # fmt: skip
        assert expected in labels
    body = _html(at)
    assert "Lokaler Entwicklungsmodus" in body
    assert "Daten werden nur lokal gespeichert" in body
    assert "cd-page-header" in body


def test_public_report_has_no_internal_navigation():
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["r"] = "abc"
    at.run()
    assert not at.exception
    assert len(at.get("page_link")) == 0
    assert len(at.sidebar.children) == 0
    body = _html(at)
    assert "Klimabericht" in body
    assert "Lokaler Entwicklungsmodus" not in body
    assert "abc" not in body  # Token wird nie angezeigt
    assert 'stSidebar"]' in body  # Report-CSS blendet Sidebar aus


def test_styleguide_renders_all_components():
    def page():
        from climate_dashboard.ui import theme
        from climate_dashboard.ui.pages import styleguide

        theme.apply_plotly_template()
        theme.inject_css("app")
        styleguide.render()

    at = AppTest.from_function(page, default_timeout=120).run()
    assert not at.exception
    assert len(at.get("plotly_chart")) >= 9


@pytest.mark.parametrize(
    "module", ["overview", "import_wizard", "analysis", "compare", "rooms", "reports", "settings", "login"]
)
def test_placeholder_pages_have_render(module):
    import importlib

    mod = importlib.import_module(f"climate_dashboard.ui.pages.{module}")
    assert callable(mod.render)
