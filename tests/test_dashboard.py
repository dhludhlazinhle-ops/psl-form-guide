from pathlib import Path

import duckdb
from streamlit.testing.v1 import AppTest

from src import clean, model, recap

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""

APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")

MATCHES = [
    ("1", "2025-2026", "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01"),
    ("2", "2025-2026", "Orlando Pirates", "Mamelodi Sundowns", "1", "1", "2025-08-08"),
    ("3", "2026-2027", "Mamelodi Sundowns", "Kaizer Chiefs", "0", "3", "2026-08-15"),
    ("4", "2026-2027", "Kaizer Chiefs", "Orlando Pirates", None, None, "2099-01-01"),  # upcoming
]


def build_db(path, with_recap=False):
    con = duckdb.connect(str(path))
    con.execute(BRONZE_DDL)
    for event_id, season, home, away, hg, ag, d in MATCHES:
        con.execute(
            "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            [event_id, season, "1", home, away, hg, ag, d, "FT", "2026-10-01 10:00:00"],
        )
    clean.build_silver(con)
    model.build_gold(con)
    if with_recap:
        recap.save_recap(con, "2026-2027", "test-model", "some facts", "Sundowns are flying this week.")
    con.close()


def test_dashboard_renders_all_tabs_without_errors(tmp_path, monkeypatch):
    db = tmp_path / "psl.duckdb"
    build_db(db)
    monkeypatch.setenv("PSL_DB_PATH", str(db))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert len(at.tabs) == 5


def test_dashboard_shows_the_latest_recap(tmp_path, monkeypatch):
    db = tmp_path / "psl.duckdb"
    build_db(db, with_recap=True)
    monkeypatch.setenv("PSL_DB_PATH", str(db))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Sundowns are flying" in m.value for m in at.markdown)


def test_dashboard_explains_what_to_do_when_the_database_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PSL_DB_PATH", str(tmp_path / "missing.duckdb"))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "No database found" in at.error[0].value
