import json
from datetime import datetime

import duckdb
import pytest

from src import export

ALIASES = """alias,team_name
Amazulu,AmaZulu
AmaZulu FC,AmaZulu
Kaizer Chiefs,Kaizer Chiefs
Chiefs,Kaizer Chiefs
Siwelele,Siwelele
SuperSport United,Siwelele
"""


def make_gold(con, with_recap=False):
    """A tiny hand-built warehouse, so these tests do not depend on the rest of the pipeline."""
    con.execute("""CREATE TABLE gold_league_table (position INTEGER, season VARCHAR, team_name VARCHAR,
        played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER, goals_for INTEGER,
        goals_against INTEGER, goal_diff INTEGER, points INTEGER)""")
    con.executemany("INSERT INTO gold_league_table VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-2027", "Kaizer Chiefs", 2, 2, 0, 0, 5, 1, 4, 6),
        (2, "2026-2027", "AmaZulu", 2, 1, 0, 1, 3, 3, 0, 3),
        (3, "2026-2027", "Siwelele", 2, 0, 0, 2, 1, 5, -4, 0),
        (1, "2025-2026", "AmaZulu", 1, 1, 0, 0, 2, 0, 2, 3),
    ])
    con.execute("""CREATE TABLE gold_elo_current (team_name VARCHAR, elo DOUBLE,
        last_match_date DATE, rated_through_season VARCHAR, is_active BOOLEAN)""")
    con.executemany("INSERT INTO gold_elo_current VALUES (?,?,?,?,?)", [
        ("Kaizer Chiefs", 1562.94, "2026-09-20", "2026-2027", True),
        ("AmaZulu", 1500.0, "2026-09-19", "2026-2027", True),
        ("Siwelele", 1437.06, "2026-09-19", "2026-2027", True),
        ("Cape Town Spurs", 1490.0, "2025-05-01", "2026-2027", False),
    ])
    con.execute("""CREATE TABLE gold_elo_history (match_id BIGINT, season VARCHAR, match_date DATE,
        team VARCHAR, opponent VARCHAR, is_home BOOLEAN, rating_before DOUBLE, rating_after DOUBLE,
        expected_score DOUBLE, actual_score DOUBLE)""")
    con.executemany("INSERT INTO gold_elo_history VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-2027", "2026-09-12", "Kaizer Chiefs", "Siwelele", True, 1500, 1531.0, .58, 1),
        (1, "2026-2027", "2026-09-12", "Siwelele", "Kaizer Chiefs", False, 1500, 1469.0, .42, 0),
        (2, "2026-2027", "2026-09-20", "Kaizer Chiefs", "AmaZulu", True, 1531.0, 1562.94, .6, 1),
        (9, "2025-2026", "2025-05-01", "Cape Town Spurs", "AmaZulu", True, 1500, 1490.0, .5, 0),
    ])
    con.execute("""CREATE TABLE gold_form (team_name VARCHAR, form_last5 VARCHAR, points_last5 INTEGER,
        matches_counted INTEGER, last_match_date DATE, is_active BOOLEAN)""")
    con.executemany("INSERT INTO gold_form VALUES (?,?,?,?,?,?)", [
        ("Kaizer Chiefs", "WW", 6, 2, "2026-09-20", True),
        ("AmaZulu", "LW", 3, 2, "2026-09-19", True),
    ])
    con.execute("""CREATE TABLE gold_fixture_predictions (match_id BIGINT, season VARCHAR, round INTEGER,
        match_date DATE, home_team VARCHAR, away_team VARCHAR, home_elo DOUBLE, away_elo DOUBLE,
        p_home DOUBLE, p_draw DOUBLE, p_away DOUBLE)""")
    con.execute("""INSERT INTO gold_fixture_predictions VALUES
        (50, '2026-2027', 3, '2026-10-17', 'AmaZulu', 'Kaizer Chiefs', 1500, 1562.9, .3333, .3, .3667)""")
    con.execute("""CREATE TABLE silver_matches (match_id BIGINT, season VARCHAR, round INTEGER,
        match_date DATE, home_team VARCHAR, away_team VARCHAR, home_goals INTEGER,
        away_goals INTEGER, is_played BOOLEAN, status VARCHAR)""")
    con.executemany("INSERT INTO silver_matches VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-2027", 1, "2026-09-12", "Kaizer Chiefs", "Siwelele", 3, 0, True, "FT"),
        (2, "2026-2027", 2, "2026-09-20", "Kaizer Chiefs", "AmaZulu", 2, 1, True, "FT"),
        (50, "2026-2027", 3, "2026-10-17", "AmaZulu", "Kaizer Chiefs", None, None, False, "NS"),
        (9, "2025-2026", 1, "2025-05-01", "Cape Town Spurs", "AmaZulu", 0, 1, True, "FT"),
    ])
    con.execute("CREATE TABLE bronze_events (_loaded_at TIMESTAMP)")
    con.execute("INSERT INTO bronze_events VALUES ('2026-10-22 11:00:00')")
    if with_recap:
        con.execute("""CREATE TABLE gold_weekly_recap (generated_at TIMESTAMP, season VARCHAR,
            model VARCHAR, facts VARCHAR, recap VARCHAR)""")
        con.execute("INSERT INTO gold_weekly_recap VALUES ('2026-10-22 12:00:00', '2026-2027', "
                    "'test-model', 'facts', 'Chiefs are flying.')")


@pytest.fixture
def con():
    c = duckdb.connect()
    make_gold(c)
    return c


def test_payload_contains_everything_the_site_shows(con):
    p = export.build_payload(con, {}, now=datetime(2026, 10, 22, 12, 0))
    assert p["meta"]["season"] == "2026-2027"
    assert p["meta"]["lastResult"] == "2026-09-20"
    assert p["meta"]["generatedAt"] == "2026-10-22T12:00"
    assert p["seasons"] == ["2026-2027", "2025-2026"]
    assert p["tables"]["2026-2027"][0] == {
        "pos": 1, "team": "Kaizer Chiefs", "p": 2, "w": 2, "d": 0, "l": 0,
        "gf": 5, "ga": 1, "gd": 4, "pts": 6}
    chiefs = p["teams"]["Kaizer Chiefs"]
    assert (chiefs["elo"], chiefs["form"], chiefs["rank"], chiefs["active"]) == (1562.9, "WW", 1, True)
    assert p["teams"]["Cape Town Spurs"]["active"] is False
    assert p["fixtures"] == [["2026-10-17", "AmaZulu", "Kaizer Chiefs", 0.333, 0.3, 0.367, 1500, 1562.9]]


def test_elo_history_is_only_kept_for_active_teams(con):
    p = export.build_payload(con, {})
    assert set(p["elo"]) == {"Kaizer Chiefs", "Siwelele"}
    assert p["elo"]["Kaizer Chiefs"] == [["2026-09-12", 1531.0], ["2026-09-20", 1562.9]]


def test_results_cover_the_latest_two_seasons_oldest_first(con):
    p = export.build_payload(con, {})
    assert [r[0] for r in p["results"]] == ["2025-05-01", "2026-09-12", "2026-09-20"]
    assert p["results"][-1] == ["2026-09-20", "Kaizer Chiefs", "AmaZulu", 2, 1]


def test_upcoming_matches_are_not_listed_as_results(con):
    p = export.build_payload(con, {})
    assert all(r[3] is not None for r in p["results"])


def test_recap_is_none_until_one_exists_then_the_newest_is_used():
    c = duckdb.connect()
    make_gold(c)
    assert export.build_payload(c, {})["recap"] is None
    c2 = duckdb.connect()
    make_gold(c2, with_recap=True)
    recap = export.build_payload(c2, {})["recap"]
    assert recap == {"generatedAt": "2026-10-22T12:00", "model": "test-model", "text": "Chiefs are flying."}


def test_empty_gold_tables_give_a_helpful_error():
    c = duckdb.connect()
    make_gold(c)
    c.execute("DELETE FROM gold_league_table")
    with pytest.raises(ValueError, match="python -m src.pipeline"):
        export.build_payload(c, {})


def write_events(folder, name, events):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(events), encoding="utf-8")


def test_badges_use_the_alias_table_and_the_newest_season_wins(tmp_path):
    alias_csv = tmp_path / "aliases.csv"
    alias_csv.write_text(ALIASES, encoding="utf-8")
    bronze = tmp_path / "bronze"
    write_events(bronze, "events_2024-2025_20261022T100000.json", [
        {"strHomeTeam": "SuperSport United", "strHomeTeamBadge": "https://x/old-supersport.png",
         "strAwayTeam": "Amazulu", "strAwayTeamBadge": "https://x/amazulu.png"}])
    write_events(bronze, "events_2026-2027_20261022T090000.json", [
        {"strHomeTeam": "Siwelele", "strHomeTeamBadge": "https://x/siwelele-new.png",
         "strAwayTeam": "Unknown FC", "strAwayTeamBadge": "https://x/unknown.png"}])
    badges = export.load_badges(str(bronze), str(alias_csv))
    assert badges == {"AmaZulu": "https://x/amazulu.png", "Siwelele": "https://x/siwelele-new.png"}


def test_badges_are_empty_when_there_are_no_raw_files(tmp_path):
    alias_csv = tmp_path / "aliases.csv"
    alias_csv.write_text(ALIASES, encoding="utf-8")
    assert export.load_badges(str(tmp_path / "nothing-here"), str(alias_csv)) == {}


def test_data_js_can_be_read_back_exactly(con, tmp_path):
    payload = export.build_payload(con, {"AmaZulu": "https://x/amazulu.png"})
    out = tmp_path / "docs" / "data.js"
    size = export.write_data_js(payload, str(out))
    text = out.read_text(encoding="utf-8")
    assert size == len(text.encode("utf-8"))
    assert text.startswith("window.PSL_DATA = ") and text.endswith(";\n")
    assert json.loads(text[len("window.PSL_DATA = "):-2]) == payload


def test_run_writes_the_site_data_from_a_database_file(tmp_path, capsys):
    db = str(tmp_path / "psl.duckdb")
    c = duckdb.connect(db)
    make_gold(c)
    c.close()
    alias_csv = tmp_path / "aliases.csv"
    alias_csv.write_text(ALIASES, encoding="utf-8")
    out = tmp_path / "site" / "data.js"
    export.run(db, str(tmp_path / "bronze"), str(alias_csv), str(out))
    assert out.exists()
    printed = capsys.readouterr().out
    assert "badges for 0 of 3 teams" in printed
    assert "No badge found" in printed
