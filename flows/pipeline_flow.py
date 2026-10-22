"""Day 4 - Prefect flow: the same pipeline, with retries, logging and an optional schedule.

python -m flows.pipeline_flow                 run once (current season)
python -m flows.pipeline_flow --skip-ingest   rebuild from existing bronze data
python -m flows.pipeline_flow --recap         also write the weekly recap (needs ANTHROPIC_API_KEY)
python -m flows.pipeline_flow --serve         run every day at 06:00 (keeps running)
"""
import sys

from prefect import flow, task

from src import ingest, pipeline


@task(retries=2, retry_delay_seconds=30)
def ingest_task(seasons: list[str]) -> int:
    # Safe to retry: re-loading a season only adds duplicate bronze rows, and silver
    # keeps the newest copy of every match.
    return pipeline.step_ingest(seasons)


@task
def clean_task() -> dict:
    return pipeline.step_clean()


@task
def model_task() -> dict:
    return pipeline.step_model()


@task
def recap_task() -> dict:
    return pipeline.step_recap()


@flow(name="psl-form-guide", log_prints=True)
def psl_flow(seasons: list[str] | None = None, skip_ingest: bool = False,
             with_recap: bool = False) -> dict:
    summary: dict = {}
    if not skip_ingest:
        summary["bronze_rows_loaded"] = ingest_task(seasons or [ingest.current_season()])
    summary.update(clean_task())  # raises DataQualityError, so model_task never runs on bad data
    summary.update(model_task())
    if with_recap:
        summary.update(recap_task())
    print("done:", summary)
    return summary


if __name__ == "__main__":
    if "--serve" in sys.argv:
        psl_flow.serve(name="psl-daily", cron="0 6 * * *")
    else:
        psl_flow(skip_ingest="--skip-ingest" in sys.argv, with_recap="--recap" in sys.argv)
