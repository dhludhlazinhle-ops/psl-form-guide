"""Day 3 - Model: silver -> gold. Star schema, league table, form, Elo and predictions."""
from datetime import date

import duckdb
import pandas as pd

from src import elo

DB_PATH = "data/psl.duckdb"
DEFAULT_DRAW_RATE = 0.27  # used only if there are no played matches to measure it from


def build_active_teams(con) -> None:
    """Teams that appear in the latest season. Anything else is a past club (relegated, sold, ...)."""
    con.execute("""
        CREATE OR REPLACE TABLE gold_active_teams AS
        SELECT DISTINCT team_name, season
        FROM (
            SELECT home_team AS team_name, season FROM silver_matches
            UNION ALL
            SELECT away_team, season FROM silver_matches
        )
        WHERE season = (SELECT max(season) FROM silver_matches)
        ORDER BY team_name
    """)


def build_dimensions_and_fact(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE dim_date AS
        SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key,
               CAST(d AS DATE) AS date,
               year(d) AS year,
               month(d) AS month,
               dayname(d) AS day_name
        FROM (
            SELECT unnest(generate_series(lo, hi, INTERVAL 1 DAY)) AS d
            FROM (SELECT min(match_date) AS lo, max(match_date) AS hi FROM silver_matches)
        )
    """)
    con.execute("""
        CREATE OR REPLACE TABLE fact_match AS
        SELECT m.match_id,
               m.season,
               m.round,
               CAST(strftime(m.match_date, '%Y%m%d') AS INTEGER) AS date_key,
               h.team_key AS home_team_key,
               a.team_key AS away_team_key,
               m.home_goals,
               m.away_goals,
               CASE WHEN NOT m.is_played THEN NULL
                    WHEN m.home_goals > m.away_goals THEN 'H'
                    WHEN m.home_goals = m.away_goals THEN 'D'
                    ELSE 'A' END AS result,
               m.is_played
        FROM silver_matches m
        JOIN dim_team h ON h.team_name = m.home_team
        JOIN dim_team a ON a.team_name = m.away_team
    """)


def build_league_table(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE gold_league_table AS
        WITH results AS (
            SELECT season, home_team AS team, home_goals AS gf, away_goals AS ga
            FROM silver_matches WHERE is_played
            UNION ALL
            SELECT season, away_team, away_goals, home_goals
            FROM silver_matches WHERE is_played
        ),
        agg AS (
            SELECT season, team AS team_name,
                   CAST(COUNT(*) AS INTEGER) AS played,
                   CAST(COUNT(*) FILTER (WHERE gf > ga) AS INTEGER) AS won,
                   CAST(COUNT(*) FILTER (WHERE gf = ga) AS INTEGER) AS drawn,
                   CAST(COUNT(*) FILTER (WHERE gf < ga) AS INTEGER) AS lost,
                   CAST(SUM(gf) AS INTEGER) AS goals_for,
                   CAST(SUM(ga) AS INTEGER) AS goals_against,
                   CAST(SUM(gf) - SUM(ga) AS INTEGER) AS goal_diff,
                   CAST(SUM(CASE WHEN gf > ga THEN 3 WHEN gf = ga THEN 1 ELSE 0 END) AS INTEGER) AS points
            FROM results GROUP BY season, team
        )
        SELECT row_number() OVER (
                   PARTITION BY season
                   ORDER BY points DESC, goal_diff DESC, goals_for DESC, team_name
               ) AS position,
               *
        FROM agg
        ORDER BY season, position
    """)


def build_form(con) -> None:
    """Each team's last 5 played matches, oldest to newest, e.g. 'WWDLW'."""
    con.execute("""
        CREATE OR REPLACE TABLE gold_form AS
        WITH team_matches AS (
            SELECT match_id, match_date, home_team AS team,
                   CASE WHEN home_goals > away_goals THEN 'W'
                        WHEN home_goals = away_goals THEN 'D' ELSE 'L' END AS res
            FROM silver_matches WHERE is_played
            UNION ALL
            SELECT match_id, match_date, away_team,
                   CASE WHEN away_goals > home_goals THEN 'W'
                        WHEN away_goals = home_goals THEN 'D' ELSE 'L' END
            FROM silver_matches WHERE is_played
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                       PARTITION BY team ORDER BY match_date DESC, match_id DESC) AS rn
            FROM team_matches
        )
        SELECT team AS team_name,
               string_agg(res, '' ORDER BY match_date, match_id) AS form_last5,
               CAST(SUM(CASE res WHEN 'W' THEN 3 WHEN 'D' THEN 1 ELSE 0 END) AS INTEGER) AS points_last5,
               CAST(COUNT(*) AS INTEGER) AS matches_counted,
               MAX(match_date) AS last_match_date,
               team IN (SELECT team_name FROM gold_active_teams) AS is_active
        FROM ranked
        WHERE rn <= 5
        GROUP BY team
        ORDER BY is_active DESC, points_last5 DESC, team_name
    """)


def build_elo_and_predictions(con, as_of: date) -> None:
    played = con.execute("""
        SELECT match_id, season, match_date, home_team, away_team, home_goals, away_goals
        FROM silver_matches
        WHERE is_played
        ORDER BY match_date, round, match_id
    """).fetchall()

    history, ratings, last_season = elo.run_elo(played)
    history_df = pd.DataFrame(history, columns=elo.HISTORY_COLUMNS)
    con.execute("CREATE OR REPLACE TABLE gold_elo_history AS SELECT * FROM history_df")

    # Current ratings come from the final ratings dictionary, so every team has been
    # regressed at the same season boundaries (a team's last-match snapshot would not be).
    last_played = {}
    for row in history:  # history is chronological
        last_played[row[3]] = row[2]
    active = {r[0] for r in con.execute("SELECT team_name FROM gold_active_teams").fetchall()}
    current_rows = sorted(
        ((team, round(r, 1), last_played[team], last_season, team in active)
         for team, r in ratings.items()),
        key=lambda x: -x[1],
    )
    current_df = pd.DataFrame(
        current_rows,
        columns=["team_name", "elo", "last_match_date", "rated_through_season", "is_active"],
    )
    con.execute("CREATE OR REPLACE TABLE gold_elo_current AS SELECT * FROM current_df")

    draw_rate = con.execute("""
        SELECT COALESCE(AVG(CASE WHEN home_goals = away_goals THEN 1.0 ELSE 0.0 END), ?)
        FROM silver_matches WHERE is_played
    """, [DEFAULT_DRAW_RATE]).fetchone()[0]

    upcoming = con.execute("""
        SELECT match_id, season, round, match_date, home_team, away_team
        FROM silver_matches
        WHERE NOT is_played AND match_date >= ?
        ORDER BY match_date, match_id
    """, [as_of]).fetchall()

    rows = []
    for match_id, season, rnd, match_date, home, away in upcoming:
        # A fixture in a season that has not started yet gets the same
        # start-of-season regression that the Elo replay would apply.
        r = elo.regress(ratings) if season != last_season else ratings
        r_home = r.get(home, elo.START_RATING)
        r_away = r.get(away, elo.START_RATING)
        p_home, p_draw, p_away = elo.three_way(elo.expected_home(r_home, r_away), draw_rate)
        rows.append((match_id, season, rnd, match_date, home, away,
                     round(r_home, 1), round(r_away, 1), p_home, p_draw, p_away))

    pred_df = pd.DataFrame(rows, columns=[
        "match_id", "season", "round", "match_date", "home_team", "away_team",
        "home_elo", "away_elo", "p_home", "p_draw", "p_away",
    ])
    con.execute("CREATE OR REPLACE TABLE gold_fixture_predictions AS SELECT * FROM pred_df")


def build_gold(con, as_of: date | None = None) -> None:
    """Rebuild every gold table from silver. Safe to run any number of times."""
    n = con.execute("SELECT COUNT(*) FROM silver_matches WHERE is_played").fetchone()[0]
    if n == 0:
        raise ValueError("silver_matches has no played matches - run src.clean first")
    build_active_teams(con)
    build_dimensions_and_fact(con)
    build_league_table(con)
    build_form(con)
    build_elo_and_predictions(con, as_of or date.today())


def main() -> None:
    con = duckdb.connect(DB_PATH)
    build_gold(con)

    latest = con.execute("SELECT max(season) FROM gold_league_table").fetchone()[0]
    print(f"\nLEAGUE TABLE {latest}")
    print(con.sql(f"""
        SELECT position, team_name, played, won, drawn, lost, goal_diff, points
        FROM gold_league_table WHERE season = '{latest}' ORDER BY position
    """).df().to_string(index=False))

    print("\nTOP 8 BY ELO (active teams)")
    print(con.sql("""
        SELECT team_name, elo, last_match_date FROM gold_elo_current
        WHERE is_active ORDER BY elo DESC LIMIT 8
    """).df().to_string(index=False))

    print("\nFORM (last 5 played, oldest to newest)")
    print(con.sql("""
        SELECT team_name, form_last5, points_last5 FROM gold_form WHERE is_active LIMIT 8
    """).df().to_string(index=False))

    mean_elo = con.execute("SELECT avg(elo) FROM gold_elo_current").fetchone()[0]
    print(f"\nElo sanity check: average rating = {mean_elo:.1f} (should be about 1500.0)")

    n_pred = con.execute("SELECT COUNT(*) FROM gold_fixture_predictions").fetchone()[0]
    print(f"Upcoming fixtures with predictions: {n_pred}")
    if n_pred:
        print(con.sql("""
            SELECT match_date, home_team, away_team,
                   round(p_home, 2) AS p_home, round(p_draw, 2) AS p_draw, round(p_away, 2) AS p_away
            FROM gold_fixture_predictions LIMIT 8
        """).df().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()