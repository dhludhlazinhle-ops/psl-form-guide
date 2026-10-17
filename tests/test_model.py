from datetime import date

import duckdb
import pytest

from src import clean, model

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""
AS_OF = date(2026, 1, 1)


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(BRONZE_DDL)
    return c


def add(con, event_id, home, away, hg, ag, date_, season="2025-2026"):
    con.execute(
        "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        [str(event_id), season, "1", home, away, hg, ag, date_, "FT", "2026-01-01 10:00:00"],
    )


def build(con):
    clean.build_silver(con)
    model.build_gold(con, as_of=AS_OF)


def test_league_table_points_and_positions(con):
    add(con, 1, "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")
    add(con, 2, "Orlando Pirates", "Kaizer Chiefs", "1", "1", "2025-08-08")
    add(con, 3, "Mamelodi Sundowns", "Kaizer Chiefs", "0", "1", "2025-08-15")
    build(con)
    rows = con.execute(
        "SELECT position, team_name, played, points, goal_diff FROM gold_league_table ORDER BY position"
    ).fetchall()
    assert rows[0] == (1, "Kaizer Chiefs", 3, 7, 3)  # W D W
    assert rows[1] == (2, "Orlando Pirates", 2, 1, -2)
    assert rows[2] == (3, "Mamelodi Sundowns", 1, 0, -1)


def test_form_is_oldest_to_newest_and_capped_at_five(con):
    results = [("2", "0"), ("1", "1"), ("0", "1"), ("3", "0"), ("2", "2"), ("1", "0")]  # W D L W D W
    for i, (hg, ag) in enumerate(results, start=1):
        add(con, i, "Kaizer Chiefs", "Orlando Pirates", hg, ag, f"2025-08-{i:02d}")
    build(con)
    form, pts, counted = con.execute(
        "SELECT form_last5, points_last5, matches_counted FROM gold_form WHERE team_name = 'Kaizer Chiefs'"
    ).fetchone()
    assert form == "DLWDW"  # the first match (W) has dropped out
    assert (pts, counted) == (8, 5)


def test_fact_match_has_one_row_per_silver_match_with_a_result(con):
    add(con, 1, "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")
    add(con, 2, "Orlando Pirates", "Kaizer Chiefs", None, None, "2026-03-01")
    build(con)
    rows = dict(con.execute("SELECT match_id, result FROM fact_match").fetchall())
    assert rows == {1: "H", 2: None}


def test_predictions_cover_only_future_unplayed_fixtures(con):
    add(con, 1, "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")
    add(con, 2, "Orlando Pirates", "Kaizer Chiefs", None, None, "2026-03-01")  # upcoming
    add(con, 3, "Kaizer Chiefs", "Mamelodi Sundowns", None, None, "2025-09-01")  # never played, in the past
    build(con)
    rows = con.execute("SELECT match_id, p_home + p_draw + p_away FROM gold_fixture_predictions").fetchall()
    assert [r[0] for r in rows] == [2]
    assert rows[0][1] == pytest.approx(1.0)


def test_prediction_rewards_a_strong_record(con):
    others = ["Mamelodi Sundowns", "Chippa United", "Magesi"]
    for i, other in enumerate(others, start=1):
        add(con, i, "Kaizer Chiefs", other, "3", "0", f"2025-08-{i:02d}")        # Chiefs keep winning
        add(con, i + 10, "Orlando Pirates", other, "0", "2", f"2025-08-{i + 10:02d}")  # Pirates keep losing
    add(con, 99, "Kaizer Chiefs", "Orlando Pirates", None, None, "2026-03-01")   # upcoming
    build(con)
    p_home, p_away = con.execute("SELECT p_home, p_away FROM gold_fixture_predictions").fetchone()
    assert p_home > p_away
    assert p_home > 0.6  # two evenly rated teams would only give the home side about 0.59


def test_average_elo_stays_at_1500(con):
    add(con, 1, "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")
    add(con, 2, "Orlando Pirates", "Mamelodi Sundowns", "1", "1", "2025-08-08")
    add(con, 3, "Mamelodi Sundowns", "Kaizer Chiefs", "0", "3", "2025-08-15", season="2026-2027")
    build(con)
    mean_elo = con.execute("SELECT avg(elo) FROM gold_elo_current").fetchone()[0]
    assert mean_elo == pytest.approx(1500, abs=0.2)  # elo is stored rounded to 1 decimal


def test_build_gold_refuses_to_run_without_played_matches(con):
    add(con, 1, "Kaizer Chiefs", "Orlando Pirates", None, None, "2026-03-01")
    clean.build_silver(con)
    with pytest.raises(ValueError, match="no played matches"):
        model.build_gold(con, as_of=AS_OF)


def test_teams_missing_from_the_latest_season_are_marked_inactive(con):
    add(con, 1, "Kaizer Chiefs", "Cape Town Spurs", "1", "0", "2024-08-01", season="2024-2025")
    add(con, 2, "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01", season="2025-2026")
    build(con)
    form = dict(con.execute("SELECT team_name, is_active FROM gold_form").fetchall())
    elo_active = dict(con.execute("SELECT team_name, is_active FROM gold_elo_current").fetchall())
    assert form["Kaizer Chiefs"] is True and form["Cape Town Spurs"] is False
    assert elo_active["Orlando Pirates"] is True and elo_active["Cape Town Spurs"] is False