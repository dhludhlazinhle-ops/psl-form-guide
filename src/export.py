"""Export: write the gold tables as one small data.js file for the static website in docs/.

python -m src.export      run it after the pipeline (python -m src.pipeline)
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DB_PATH = "data/psl.duckdb"
BRONZE_DIR = "data/bronze"
ALIASES_CSV = "seeds/team_aliases.csv"
OUTPUT = "docs/data.js"

TABLE_COLUMNS = ["pos", "team", "p", "w", "d", "l", "gf", "ga", "gd", "pts"]


def load_badges(bronze_dir: str = BRONZE_DIR, alias_csv: str = ALIASES_CSV) -> dict[str, str]:
    """Map each team to its badge URL, read from the raw API files kept in bronze.

    Team names are standardised with the same alias table the pipeline uses. Files are read
    oldest season first, so the newest badge wins (for example Siwelele's, not SuperSport's).
    """
    with open(alias_csv, newline="", encoding="utf-8") as f:
        aliases = {row["alias"].strip().lower(): row["team_name"].strip()
                   for row in csv.DictReader(f)}
    badges: dict[str, str] = {}
    for path in sorted(Path(bronze_dir).glob("events_*.json")):
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for event in events:
            for side in ("Home", "Away"):
                team = aliases.get((event.get(f"str{side}Team") or "").strip().lower())
                url = event.get(f"str{side}TeamBadge")
                if team and url:
                    badges[team] = url
    return badges


def build_payload(con, badges: dict[str, str], now: datetime | None = None) -> dict:
    """Collect everything the website shows from the gold tables."""
    latest = con.execute("SELECT max(season) FROM gold_league_table").fetchone()[0]
    if latest is None:
        raise ValueError("The gold tables are empty - run `python -m src.pipeline` first")

    seasons = [r[0] for r in con.execute(
        "SELECT DISTINCT season FROM gold_league_table ORDER BY season DESC").fetchall()]

    tables = {}
    for season in seasons:
        rows = con.execute("""
            SELECT position, team_name, played, won, drawn, lost,
                   goals_for, goals_against, goal_diff, points
            FROM gold_league_table WHERE season = ? ORDER BY position
        """, [season]).fetchall()
        tables[season] = [dict(zip(TABLE_COLUMNS, row)) for row in rows]

    rank = {row["team"]: row["pos"] for row in tables[latest]}
    form = {name: (f, pts) for name, f, pts in con.execute(
        "SELECT team_name, form_last5, points_last5 FROM gold_form").fetchall()}
    teams = {}
    for name, elo, active in con.execute(
            "SELECT team_name, elo, is_active FROM gold_elo_current").fetchall():
        f, pts = form.get(name, ("", 0))
        teams[name] = {"badge": badges.get(name), "elo": round(elo, 1), "active": bool(active),
                       "form": f, "formPoints": pts, "rank": rank.get(name)}

    elo_history: dict[str, list] = {}
    for team, match_date, rating in con.execute("""
        SELECT team, match_date, rating_after FROM gold_elo_history
        WHERE team IN (SELECT team_name FROM gold_elo_current WHERE is_active)
        ORDER BY team, match_date, match_id
    """).fetchall():
        elo_history.setdefault(team, []).append([str(match_date), round(rating, 1)])

    recent = seasons[:2]
    marks = ",".join("?" * len(recent))
    results = [[str(d), h, a, hg, ag] for d, h, a, hg, ag in con.execute(f"""
        SELECT match_date, home_team, away_team, home_goals, away_goals
        FROM silver_matches WHERE is_played AND season IN ({marks})
        ORDER BY match_date, match_id
    """, recent).fetchall()]

    fixtures = [[str(d), h, a, round(ph, 3), round(pd_, 3), round(pa, 3), he, ae]
                for d, h, a, ph, pd_, pa, he, ae in con.execute("""
        SELECT match_date, home_team, away_team, p_home, p_draw, p_away, home_elo, away_elo
        FROM gold_fixture_predictions ORDER BY match_date, match_id
    """).fetchall()]

    recap = None
    if con.execute("SELECT COUNT(*) FROM information_schema.tables "
                   "WHERE table_name = 'gold_weekly_recap'").fetchone()[0]:
        row = con.execute("""
            SELECT generated_at, model, recap FROM gold_weekly_recap
            ORDER BY generated_at DESC LIMIT 1
        """).fetchone()
        if row:
            recap = {"generatedAt": row[0].isoformat(timespec="minutes"),
                     "model": row[1], "text": row[2]}

    last_result = con.execute(
        "SELECT max(match_date) FROM silver_matches WHERE is_played").fetchone()[0]
    try:
        last_load = con.execute("SELECT max(_loaded_at) FROM bronze_events").fetchone()[0]
    except duckdb.CatalogException:
        last_load = None

    now = now or datetime.now(timezone.utc)
    return {
        "meta": {
            "season": latest,
            "generatedAt": now.strftime("%Y-%m-%dT%H:%M"),
            "lastResult": str(last_result) if last_result else None,
            "lastLoad": last_load.isoformat(timespec="minutes") if last_load else None,
            "source": "TheSportsDB",
        },
        "seasons": seasons,
        "tables": tables,
        "teams": teams,
        "elo": elo_history,
        "results": results,
        "fixtures": fixtures,
        "recap": recap,
    }


def write_data_js(payload: dict, path: str = OUTPUT) -> int:
    """Write `window.PSL_DATA = {...};` (a script file works when index.html is opened directly)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "window.PSL_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    out.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def run(db_path: str = DB_PATH, bronze_dir: str = BRONZE_DIR,
        alias_csv: str = ALIASES_CSV, output: str = OUTPUT) -> dict:
    badges = load_badges(bronze_dir, alias_csv)
    con = duckdb.connect(db_path, read_only=True)
    try:
        payload = build_payload(con, badges)
    finally:
        con.close()
    size = write_data_js(payload, output)

    active = [name for name, t in payload["teams"].items() if t["active"]]
    missing = [name for name in active if not payload["teams"][name]["badge"]]
    print(f"Wrote {output} ({size / 1024:.0f} KB): {len(active)} active teams, "
          f"{len(payload['fixtures'])} upcoming fixtures, "
          f"badges for {len(active) - len(missing)} of {len(active)} teams")
    if missing:
        print("No badge found (the site shows a generated crest instead):", ", ".join(missing))
    return payload


def main() -> None:
    run()


if __name__ == "__main__":
    main()
