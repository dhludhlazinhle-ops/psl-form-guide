"""Day 4 - Pipeline: ingest -> clean -> model, in order, stopping on the first failure."""
import argparse
import time

import duckdb

from src import clean, ingest, model, recap


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


def run_pipeline(seasons: list[str] | None = None, skip_ingest: bool = False,
                 with_recap: bool = False) -> dict:
    """Refresh everything. By default only the current season is re-fetched."""
    started = time.time()
    summary: dict = {}
    total = 4 if with_recap else 3

    if skip_ingest:
        print(f"[1/{total}] ingest skipped")
    else:
        seasons = seasons or [ingest.current_season()]
        print(f"[1/{total}] ingest {seasons}")
        summary["bronze_rows_loaded"] = step_ingest(seasons)

    print(f"[2/{total}] clean + quality checks")
    summary.update(step_clean())

    print(f"[3/{total}] model")
    summary.update(step_model())

    if with_recap:
        print(f"[4/{total}] weekly recap")
        summary.update(step_recap())

    summary["seconds"] = round(time.time() - started, 1)
    print("done:", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the PSL Form Guide data.")
    parser.add_argument("--all", action="store_true",
                        help="re-fetch every season (about 6 minutes)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="rebuild silver and gold from the bronze data you already have")
    parser.add_argument("--recap", action="store_true",
                        help="also write the weekly recap with Claude (needs ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    run_pipeline(seasons=ingest.SEASONS if args.all else None,
                 skip_ingest=args.skip_ingest, with_recap=args.recap)


if __name__ == "__main__":
    main()
