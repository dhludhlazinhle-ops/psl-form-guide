"""Ingest: pull PSL match data from TheSportsDB and land it raw (bronze)."""
import json
import sys
import time
from datetime import date, datetime, timezone
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


def _retry_after(resp, default: float) -> float:
    """How long the API asked us to wait (Retry-After header), capped at two minutes."""
    try:
        return min(float(resp.headers.get("Retry-After", default)), 120.0)
    except (TypeError, ValueError):
        return default


def fetch_round(season: str, rnd: int, retries: int = 6) -> list[dict]:
    """Fetch all matches for one round. Waits when rate limited, raises if it keeps failing."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                f"{BASE_URL}/eventsround.php",
                params={"id": LEAGUE_ID, "r": rnd, "s": season},
                timeout=20,
            )
            if resp.status_code == 429:  # rate limited: wait, a bit longer each attempt
                wait = _retry_after(resp, default=min(30 * attempt, 120))
                print(f"  rate limited (429) on {season} round {rnd}; "
                      f"waiting {wait:.0f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("events") or []
        except (requests.RequestException, ValueError) as exc:
            print(f"  attempt {attempt}/{retries} failed for {season} round {rnd}: {exc}")
            time.sleep(2 * attempt)
    raise RuntimeError(f"Giving up on {season} round {rnd} after {retries} attempts")


def fetch_season(season: str) -> list[dict]:
    events: list[dict] = []
    for rnd in range(1, MAX_ROUNDS + 1):
        events.extend(fetch_round(season, rnd))
        time.sleep(REQUEST_DELAY)
    return events


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


def current_season(today: date | None = None) -> str:
    """PSL seasons run August to May, so from July onwards we are in the new season."""
    today = today or date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start}-{start + 1}"


def run(seasons: list[str] | None = None) -> int:
    """Fetch the given seasons (default: all) and append them to bronze. Returns rows loaded.

    Each season is loaded as soon as it has been fetched, so a failure part-way through
    keeps the seasons that finished. Re-running is safe: silver keeps only the newest
    copy of every match.
    """
    seasons = seasons or SEASONS
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stamp = loaded_at.strftime("%Y%m%dT%H%M%S")

    total = 0
    for season in seasons:
        events = fetch_season(season)
        if not events:
            print(f"{season}: no matches returned, skipping")
            continue
        # Keep the untouched raw response on disk
        (BRONZE_DIR / f"events_{season}_{stamp}.json").write_text(json.dumps(events, indent=2))
        con = duckdb.connect(DB_PATH)
        try:
            n = load_bronze(con, events, loaded_at)
        finally:
            con.close()
        print(f"{season}: loaded {n} matches")
        total += n

    if total == 0:
        raise RuntimeError("No events returned - check the league id, seasons and API limits.")
    return total


def main() -> None:
    """python -m src.ingest [season ...]   e.g. python -m src.ingest 2026-2027"""
    run(sys.argv[1:] or None)


if __name__ == "__main__":
    main()