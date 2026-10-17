"""Day 1 - Ingest: pull PSL match data from TheSportsDB and land it raw (bronze)."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import requests

API_KEY = "123"  # TheSportsDB public test key - check their docs for current limits
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"
LEAGUE_ID = 4802  # South African Premier Soccer League
SEASONS = ["2023-2024", "2024-2025", "2025-2026", "2026-2027"]
MAX_ROUNDS = 30  # 16 teams -> 30 rounds per season
REQUEST_DELAY = 2.5  # seconds between calls, to respect the free rate limit

BRONZE_DIR = Path("data/bronze")
DB_PATH = "data/psl.duckdb"

# The raw fields we keep in the bronze table (the JSON files keep everything)
KEEP_COLS = [
    "idEvent", "strEvent", "strLeague", "strSeason", "intRound",
    "strHomeTeam", "strAwayTeam", "idHomeTeam", "idAwayTeam",
    "intHomeScore", "intAwayScore", "dateEvent", "strTime", "strStatus",
]


def fetch_round(season: str, rnd: int, retries: int = 3) -> list[dict]:
    """Fetch all matches for one round. Retries on errors, raises if it keeps failing."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                f"{BASE_URL}/eventsround.php",
                params={"id": LEAGUE_ID, "r": rnd, "s": season},
                timeout=20,
            )
            if resp.status_code == 429:  # rate limited: wait longer each attempt
                time.sleep(10 * attempt)
                continue
            resp.raise_for_status()
            return resp.json().get("events") or []
        except (requests.RequestException, ValueError) as exc:
            print(f"  attempt {attempt} failed for {season} round {rnd}: {exc}")
            time.sleep(2 * attempt)
    raise RuntimeError(f"Giving up on {season} round {rnd}")


def load_bronze(con, events: list[dict], loaded_at: datetime) -> int:
    """Append raw events to bronze_events. Bronze is append-only: never edit, never delete."""
    cols_ddl = ", ".join(f"{c} VARCHAR" for c in KEEP_COLS)
    con.execute(
        f"CREATE TABLE IF NOT EXISTS bronze_events ({cols_ddl}, _loaded_at TIMESTAMP, _source VARCHAR)"
    )
    df = pd.DataFrame(events).reindex(columns=KEEP_COLS)
    df = df.astype(object).where(df.notna(), None)
    df["_loaded_at"] = loaded_at
    df["_source"] = "thesportsdb"
    con.execute("INSERT INTO bronze_events SELECT * FROM df")
    return len(df)


def main() -> None:
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stamp = loaded_at.strftime("%Y%m%dT%H%M%S")

    all_events: list[dict] = []
    for season in SEASONS:
        season_events: list[dict] = []
        for rnd in range(1, MAX_ROUNDS + 1):
            season_events.extend(fetch_round(season, rnd))
            time.sleep(REQUEST_DELAY)
        print(f"{season}: {len(season_events)} matches")
        # Keep the untouched raw response on disk
        (BRONZE_DIR / f"events_{season}_{stamp}.json").write_text(
            json.dumps(season_events, indent=2)
        )
        all_events.extend(season_events)

    if not all_events:
        raise SystemExit("No events returned - check the league id, seasons and API limits.")

    con = duckdb.connect(DB_PATH)
    n = load_bronze(con, all_events, loaded_at)
    print(f"Loaded {n} rows into bronze_events")
    con.close()


if __name__ == "__main__":
    main()