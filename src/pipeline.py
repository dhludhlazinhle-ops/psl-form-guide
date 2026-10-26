"""Pipeline: ingest -> clean -> model (-> recap -> export), in order, stopping on the first failure."""
import argparse
import time
from pathlib import Path

import duckdb

from src import clean, export, ingest, model, recap


class DataQualityError(RuntimeError):
    """Raised when silver fails its quality checks, so bad data never reaches gold."""


def step_ingest(seasons: list[str] | None = None) -> int:
    return ingest.run(seasons)


def step_clean(db_path: str = clean.DB_PATH) -> dict:
    con = duckdb.connect(db_path)
    try:
        clean.build_silver(con)
        failures = clean.run_checks(con)
        if failures:
            details = ", ".join(f"{name} ({bad} rows)" for name, bad in failures.items())
            raise DataQualityError(f"Data quality checks failed: {details}")
        total = con.execute("SELECT COUNT(*) FROM silver_matches").fetchone()[0]
        quarantined = con.execute("SELECT COUNT(*) FROM silver_quarantine").fetchone()[0]
        return {"silver_matches": total, "quarantined": quarantined}
    finally:
        con.close()


def step_model(db_path: str = model.DB_PATH) -> dict:
    con = duckdb.connect(db_path)
    try:
        model.build_gold(con)
        predictions = con.execute("SELECT COUNT(*) FROM gold_fixture_predictions").fetchone()[0]
        return {"predictions": predictions}
    finally:
        con.close()


def step_recap(db_path: str = recap.DB_PATH) -> dict:
    return recap.run(db_path)


def step_export() -> dict:
    export.run()
    return {"site_data": "exported"}


def missing_seasons(db_path: str = ingest.DB_PATH, seasons: list[str] | None = None) -> list[str]:
    """Seasons that have no rows in bronze yet (all of them when there is no database)."""
    wanted = list(seasons or ingest.SEASONS)
    if not Path(db_path).exists():
        return wanted
    con = duckdb.connect(db_path, read_only=True)
    try:
        have = {row[0] for row in con.execute("SELECT DISTINCT strSeason FROM bronze_events").fetchall()}
    except duckdb.CatalogException:  # database exists but nothing has been loaded yet
        have = set()
    finally:
        con.close()
    return [season for season in wanted if season not in have]


def run_pipeline(seasons: list[str] | None = None, skip_ingest: bool = False,
                 with_recap: bool = False, with_export: bool = False) -> dict:
    """Refresh everything. By default only the current season is re-fetched."""
    started = time.time()
    summary: dict = {}
    total = 3 + int(with_recap) + int(with_export)
    step = 0

    def announce(text: str) -> None:
        nonlocal step
        step += 1
        print(f"[{step}/{total}] {text}")

    if skip_ingest:
        print(f"[ingest skipped] {total} steps follow")
    else:
        seasons = seasons or [ingest.current_season()]
        announce(f"ingest {seasons}")
        summary["bronze_rows_loaded"] = step_ingest(seasons)

    announce("clean + quality checks")
    summary.update(step_clean())

    announce("model")
    summary.update(step_model())

    if with_recap:
        announce("weekly recap")
        summary.update(step_recap())

    if with_export:
        announce("export site data")
        summary.update(step_export())

    summary["seconds"] = round(time.time() - started, 1)
    print("done:", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the PSL Form Guide data.")
    parser.add_argument("--all", action="store_true",
                        help="re-fetch every season (about 6 minutes)")
    parser.add_argument("--auto", action="store_true",
                        help="fetch the current season plus any season missing from the database "
                             "(this is what the scheduled GitHub Action uses)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="rebuild silver and gold from the bronze data you already have")
    parser.add_argument("--recap", action="store_true",
                        help="also write the weekly recap with Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--export", action="store_true",
                        help="also write docs/data.js for the website")
    args = parser.parse_args()

    seasons = None
    if args.all:
        seasons = ingest.SEASONS
    elif args.auto:
        seasons = sorted(set(missing_seasons()) | {ingest.current_season()})
    run_pipeline(seasons=seasons, skip_ingest=args.skip_ingest,
                 with_recap=args.recap, with_export=args.export)


if __name__ == "__main__":
    main()
