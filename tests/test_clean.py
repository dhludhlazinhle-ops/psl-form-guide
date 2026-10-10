import duckdb
import pytest

from src import clean

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""


@pytest.fixture
def con():
    c = duckdb.connect()  # fresh in-memory database for every test
    c.execute(BRONZE_DDL)
    return c


def add(con, event_id="1", home="Amazulu", away="Kaizer Chiefs",
        hg="2", ag="1", date="2025-08-08", loaded="2026-01-01 10:00:00"):
    con.execute(
        "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        [event_id, "2025-2026", "1", home, away, hg, ag, date, "Match Finished", loaded],
    )


def test_team_name_variants_become_one_team(con):
    add(con, "1", home="Amazulu")
    add(con, "2", home="AmaZulu FC", away="Chiefs")
    clean.build_silver(con)
    homes = {r[0] for r in con.execute("SELECT home_team FROM silver_matches").fetchall()}
    assert homes == {"AmaZulu"}


def test_rerunning_does_not_duplicate_matches(con):
    add(con, "1")
    add(con, "1", loaded="2026-01-02 10:00:00")  # same match loaded twice
    clean.build_silver(con)
    clean.build_silver(con)  # and the cleaning step run twice
    assert con.execute("SELECT COUNT(*) FROM silver_matches").fetchone()[0] == 1


def test_latest_load_wins(con):
    add(con, "1", hg="0", ag="0", loaded="2026-01-01 10:00:00")
    add(con, "1", hg="3", ag="1", loaded="2026-01-02 10:00:00")  # corrected score
    clean.build_silver(con)
    row = con.execute("SELECT home_goals, away_goals FROM silver_matches").fetchone()
    assert row == (3, 1)


def test_unmapped_team_fails_loudly(con):
    add(con, "1", home="Totally New FC")
    with pytest.raises(ValueError, match="Totally New FC"):
        clean.build_silver(con)


def test_unplayed_match_is_flagged(con):
    add(con, "1", hg=None, ag=None)
    clean.build_silver(con)
    assert con.execute("SELECT is_played FROM silver_matches").fetchone()[0] is False


def test_absurd_score_fails_quality_check(con):
    add(con, "1", hg="99", ag="0")
    clean.build_silver(con)
    assert "goals are in a sane range (0-15)" in clean.run_checks(con)


def test_good_data_passes_all_checks(con):
    add(con, "1")
    clean.build_silver(con)
    assert clean.run_checks(con) == {}


def test_stale_placeholder_is_quarantined_when_played_twin_exists(con):
    add(con, "1", hg="1", ag="1")    # the real, played match
    add(con, "2", hg=None, ag=None)  # stale 'not started' copy of the same fixture
    clean.build_silver(con)
    assert con.execute("SELECT COUNT(*) FROM silver_matches").fetchone()[0] == 1
    assert con.execute("SELECT is_played FROM silver_matches").fetchone()[0] is True
    assert con.execute("SELECT COUNT(*) FROM silver_quarantine").fetchone()[0] == 1


def test_genuinely_upcoming_match_is_kept(con):
    add(con, "1", hg=None, ag=None)
    clean.build_silver(con)
    assert con.execute("SELECT COUNT(*) FROM silver_matches").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM silver_quarantine").fetchone()[0] == 0


def test_two_played_matches_for_same_pair_still_fail_the_check(con):
    add(con, "1", hg="0", ag="0", date="2025-08-27")
    add(con, "2", hg="1", ag="1", date="2026-05-06")
    clean.build_silver(con)
    assert "one match per home/away pair per season" in clean.run_checks(con)


def test_mirrored_duplicate_fixture_is_quarantined(con):
    # Same two teams, same day, same score, home/away reversed (like the Chiefs/Sundowns case)
    add(con, "1", home="Mamelodi Sundowns", away="Kaizer Chiefs", hg="0", ag="0", date="2025-08-27")
    add(con, "2", home="Kaizer Chiefs", away="Mamelodi Sundowns", hg="0", ag="0", date="2025-08-27")
    clean.build_silver(con)
    assert con.execute("SELECT match_id FROM silver_matches").fetchall() == [(1,)]
    reason = con.execute("SELECT reason FROM silver_quarantine").fetchone()[0]
    assert reason.startswith("duplicate fixture")
    assert clean.run_checks(con) == {}


def test_supersport_united_is_mapped_to_siwelele(con):
    add(con, "1", home="SuperSport United", away="Kaizer Chiefs")
    add(con, "2", home="Siwelele FC", away="Chiefs")
    clean.build_silver(con)
    homes = {r[0] for r in con.execute("SELECT home_team FROM silver_matches").fetchall()}
    assert homes == {"Siwelele"}