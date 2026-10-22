import duckdb
import pytest

from src import pipeline

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""


def make_db(tmp_path, rows):
    path = str(tmp_path / "test.duckdb")
    con = duckdb.connect(path)
    con.execute(BRONZE_DDL)
    for i, (home, away, hg, ag, d) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            [str(i), "2025-2026", "1", home, away, hg, ag, d, "FT", "2026-01-01 10:00:00"],
        )
    con.close()
    return path


def table_exists(path, name):
    con = duckdb.connect(path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_clean_then_model_builds_every_layer(tmp_path):
    path = make_db(tmp_path, [("Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")])
    assert pipeline.step_clean(path)["silver_matches"] == 1
    pipeline.step_model(path)
    assert table_exists(path, "gold_league_table")


def test_bad_data_stops_the_pipeline_before_gold(tmp_path):
    path = make_db(tmp_path, [("Kaizer Chiefs", "Orlando Pirates", "99", "0", "2025-08-01")])
    with pytest.raises(pipeline.DataQualityError, match="goals are in a sane range"):
        pipeline.step_clean(path)
    assert not table_exists(path, "gold_league_table")  # gold was never built
