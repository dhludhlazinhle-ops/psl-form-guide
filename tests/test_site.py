import json
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
PREFIX = "window.PSL_DATA = "


def test_the_page_loads_its_data_file_and_needs_no_build_step():
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    assert '<script src="data.js"></script>' in html
    assert "window.PSL_DATA" in html


@pytest.mark.parametrize("tab", ["table", "power", "fixtures", "recap"])
def test_every_tab_has_a_matching_panel(tab):
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    assert f'data-tab="{tab}"' in html
    assert f'id="panel-{tab}"' in html


def test_pages_serves_the_site_as_plain_files():
    assert (DOCS / ".nojekyll").exists()


def test_committed_site_data_is_valid_when_present():
    path = DOCS / "data.js"
    if not path.exists():
        pytest.skip("docs/data.js has not been exported yet (python -m src.export)")
    text = path.read_text(encoding="utf-8")
    assert text.startswith(PREFIX)
    data = json.loads(text[len(PREFIX):].rstrip().rstrip(";"))
    for key in ("meta", "seasons", "tables", "teams", "elo", "results", "fixtures", "recap"):
        assert key in data
    assert data["tables"][data["meta"]["season"]], "the latest season has an empty table"
