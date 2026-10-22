"""Day 4 - Pipeline: ingest -> clean -> model, in order, stopping on the first failure."""
import argparse
import time

import duckdb

from src import clean, ingest, model


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


def run_pipeline(seasons: list[str] | None = None, skip_ingest: bool = False) -> dict:
    """Refresh everything. By default only the current season is re-fetched."""
    started = time.time()
    summary: dict = {}

    if skip_ingest:
        print("[1/3] ingest skipped")
    else:
        seasons = seasons or [ingest.current_season()]
        print(f"[1/3] ingest {seasons}")
        summary["bronze_rows_loaded"] = step_ingest(seasons)

    print("[2/3] clean + quality checks")
    summary.update(step_clean())

    print("[3/3] model")
    summary.update(step_model())

    summary["seconds"] = round(time.time() - started, 1)
    print("done:", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the PSL Form Guide data.")
    parser.add_argument("--all", action="store_true",
                        help="re-fetch every season (about 6 minutes)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="rebuild silver and gold from the bronze data you already have")
    args = parser.parse_args()
    run_pipeline(seasons=ingest.SEASONS if args.all else None, skip_ingest=args.skip_ingest)


if __name__ == "__main__":
    main()
