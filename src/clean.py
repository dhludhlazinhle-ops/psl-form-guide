"""Day 2 - Clean: bronze -> silver. Standardise names, dedupe, type columns, run quality checks."""
import sys

import duckdb

DB_PATH = "data/psl.duckdb"
ALIASES_CSV = "seeds/team_aliases.csv"

# Each check counts BAD rows. Every count must be 0 for the pipeline to pass.
CHECKS = {
    "no missing match dates":
        "SELECT COUNT(*) FROM silver_matches WHERE match_date IS NULL",
    "no team plays itself":
        "SELECT COUNT(*) FROM silver_matches WHERE home_team = away_team",
    "goals are in a sane range (0-15)":
        "SELECT COUNT(*) FROM silver_matches WHERE is_played "
        "AND (home_goals NOT BETWEEN 0 AND 15 OR away_goals NOT BETWEEN 0 AND 15)",
    "match_id is unique":
        "SELECT COUNT(*) - COUNT(DISTINCT match_id) FROM silver_matches",
    "one match per home/away pair per season":
        "SELECT COUNT(*) FROM (SELECT 1 FROM silver_matches "
        "GROUP BY season, home_team, away_team HAVING COUNT(*) > 1)",
}


def build_silver(con, alias_csv: str = ALIASES_CSV) -> None:
    """Rebuild dim_team and silver_matches from bronze_events. Safe to run any number of times."""
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_team_alias AS
        SELECT DISTINCT lower(trim(alias)) AS alias_key, trim(team_name) AS team_name
        FROM read_csv_auto('{alias_csv}', header = true)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE dim_team AS
        SELECT row_number() OVER (ORDER BY team_name) AS team_key, team_name
        FROM (SELECT DISTINCT team_name FROM dim_team_alias)
    """)

    # Bronze may hold the same match many times (one copy per load). Keep the newest copy.
    con.execute("""
        CREATE OR REPLACE VIEW bronze_latest AS
        SELECT * FROM bronze_events
        QUALIFY row_number() OVER (PARTITION BY idEvent ORDER BY _loaded_at DESC) = 1
    """)

    # Fail loudly if a team name has no mapping yet
    unmapped = con.execute("""
        SELECT DISTINCT name FROM (
            SELECT strHomeTeam AS name FROM bronze_latest
            UNION SELECT strAwayTeam FROM bronze_latest
        )
        WHERE name IS NOT NULL
          AND lower(trim(name)) NOT IN (SELECT alias_key FROM dim_team_alias)
        ORDER BY name
    """).fetchall()
    if unmapped:
        names = [r[0] for r in unmapped]
        raise ValueError(f"Unmapped team names - add them to {alias_csv}: {names}")

    # Step 1: type every column and flag played matches
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _typed AS
        SELECT
            CAST(b.idEvent AS BIGINT)        AS match_id,
            b.strSeason                      AS season,
            TRY_CAST(b.intRound AS INTEGER)  AS round,
            TRY_CAST(b.dateEvent AS DATE)    AS match_date,
            h.team_name                      AS home_team,
            a.team_name                      AS away_team,
            TRY_CAST(b.intHomeScore AS INTEGER) AS home_goals,
            TRY_CAST(b.intAwayScore AS INTEGER) AS away_goals,
            (TRY_CAST(b.intHomeScore AS INTEGER) IS NOT NULL
             AND TRY_CAST(b.intAwayScore AS INTEGER) IS NOT NULL) AS is_played,
            b.strStatus                      AS status
        FROM bronze_latest b
        LEFT JOIN dim_team_alias h ON lower(trim(b.strHomeTeam)) = h.alias_key
        LEFT JOIN dim_team_alias a ON lower(trim(b.strAwayTeam)) = a.alias_key
    """)

    # Step 2: flag rows that must not reach silver, with a reason for each.
    #  (a) stale placeholder: a 'not started' row whose fixture (season + home + away)
    #      also exists as a played match
    #  (b) duplicate fixture: the same two teams on the same day, in either home/away
    #      order - keep the played row, then the lowest match_id
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _flagged AS
        SELECT t.*,
            CASE
                WHEN NOT t.is_played AND EXISTS (
                    SELECT 1 FROM _typed p
                    WHERE p.is_played AND p.season = t.season
                      AND p.home_team = t.home_team AND p.away_team = t.away_team
                ) THEN 'stale placeholder: same fixture already played'
                WHEN row_number() OVER (
                    PARTITION BY t.season, t.match_date,
                                 least(t.home_team, t.away_team),
                                 greatest(t.home_team, t.away_team)
                    ORDER BY t.is_played DESC, t.match_id
                ) > 1 THEN 'duplicate fixture: same two teams on the same day'
            END AS drop_reason
        FROM _typed t
    """)

    # Step 3: split into clean rows and quarantined rows (nothing is deleted)
    con.execute("""
        CREATE OR REPLACE TABLE silver_matches AS
        SELECT * EXCLUDE (drop_reason) FROM _flagged WHERE drop_reason IS NULL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver_quarantine AS
        SELECT * EXCLUDE (drop_reason), drop_reason AS reason
        FROM _flagged WHERE drop_reason IS NOT NULL
    """)
    con.execute("DROP TABLE _flagged")
    con.execute("DROP TABLE _typed")


def run_checks(con) -> dict[str, int]:
    """Return {check name: number of bad rows} for every check that failed."""
    failures = {}
    for name, sql in CHECKS.items():
        bad = con.execute(sql).fetchone()[0]
        if bad:
            failures[name] = bad
    return failures


def main() -> None:
    con = duckdb.connect(DB_PATH)
    build_silver(con)
    total, played = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_played) FROM silver_matches"
    ).fetchone()
    quarantined = con.execute("SELECT COUNT(*) FROM silver_quarantine").fetchone()[0]
    print(f"silver_matches: {total} matches ({played} played), {quarantined} quarantined")

    failures = run_checks(con)
    if failures:
        for name, bad in failures.items():
            print(f"FAILED: {name} ({bad} bad rows)")
        sys.exit(1)
    print(f"All {len(CHECKS)} data quality checks passed")
    con.close()


if __name__ == "__main__":
    main()