import duckdb
import pytest

from src import pipeline

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""


def make_db(tmp_path, rows):
    path = str(tmp_path / "test.duckdb")
    con = duckdb.connect(path)
    con.execute(BRONZE_DDL)
    for i, (home, away, hg, ag, d) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            [str(i), "2025-2026", "1", home, away, hg, ag, d, "FT", "2026-01-01 10:00:00"],
        )
    con.close()
    return path


def table_exists(path, name):
    con = duckdb.connect(path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_clean_then_model_builds_every_layer(tmp_path):
    path = make_db(tmp_path, [("Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01")])
    assert pipeline.step_clean(path)["silver_matches"] == 1
    pipeline.step_model(path)
    assert table_exists(path, "gold_league_table")


def test_bad_data_stops_the_pipeline_before_gold(tmp_path):
    path = make_db(tmp_path, [("Kaizer Chiefs", "Orlando Pirates", "99", "0", "2025-08-01")])
    with pytest.raises(pipeline.DataQualityError, match="goals are in a sane range"):
        pipeline.step_clean(path)
    assert not table_exists(path, "gold_league_table")  # gold was never built


def bronze_with_seasons(tmp_path, seasons):
    path = str(tmp_path / "seasons.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE bronze_events (strSeason VARCHAR)")
    for season in seasons:
        con.execute("INSERT INTO bronze_events VALUES (?)", [season])
    con.close()
    return path


def test_every_season_is_missing_when_there_is_no_database(tmp_path):
    wanted = ["2025-2026", "2026-2027"]
    assert pipeline.missing_seasons(str(tmp_path / "nothing.duckdb"), wanted) == wanted


def test_every_season_is_missing_when_nothing_was_loaded_yet(tmp_path):
    path = str(tmp_path / "empty.duckdb")
    duckdb.connect(path).close()
    assert pipeline.missing_seasons(path, ["2025-2026"]) == ["2025-2026"]


def test_only_the_seasons_without_rows_are_reported_missing(tmp_path):
    path = bronze_with_seasons(tmp_path, ["2024-2025", "2026-2027"])
    wanted = ["2023-2024", "2024-2025", "2025-2026", "2026-2027"]
    assert pipeline.missing_seasons(path, wanted) == ["2023-2024", "2025-2026"]


def test_nothing_is_missing_once_every_season_is_loaded(tmp_path):
    path = bronze_with_seasons(tmp_path, ["2025-2026", "2026-2027"])
    assert pipeline.missing_seasons(path, ["2025-2026", "2026-2027"]) == []


def record_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "step_ingest", lambda seasons=None: calls.append(("ingest", seasons)) or 5)
    monkeypatch.setattr(pipeline, "step_clean", lambda: calls.append(("clean",)) or {})
    monkeypatch.setattr(pipeline, "step_model", lambda: calls.append(("model",)) or {})
    monkeypatch.setattr(pipeline, "step_recap", lambda: calls.append(("recap",)) or {})
    monkeypatch.setattr(pipeline, "step_export", lambda: calls.append(("export",)) or {})
    return calls


def test_steps_run_in_order_and_export_comes_last(monkeypatch):
    calls = record_steps(monkeypatch)
    pipeline.run_pipeline(seasons=["2026-2027"], with_recap=True, with_export=True)
    assert [c[0] for c in calls] == ["ingest", "clean", "model", "recap", "export"]
    assert calls[0] == ("ingest", ["2026-2027"])


def test_recap_and_export_only_run_when_asked(monkeypatch):
    calls = record_steps(monkeypatch)
    pipeline.run_pipeline(seasons=["2026-2027"])
    assert [c[0] for c in calls] == ["ingest", "clean", "model"]


def test_a_failed_quality_check_stops_the_run_before_the_site_is_exported(monkeypatch):
    calls = record_steps(monkeypatch)

    def failing_clean():
        raise pipeline.DataQualityError("bad data")

    monkeypatch.setattr(pipeline, "step_clean", failing_clean)
    with pytest.raises(pipeline.DataQualityError):
        pipeline.run_pipeline(seasons=["2026-2027"], with_export=True)
    assert [c[0] for c in calls] == ["ingest"]


def test_auto_mode_fetches_the_current_season_plus_anything_missing(monkeypatch):
    calls = record_steps(monkeypatch)
    monkeypatch.setattr(pipeline.ingest, "current_season", lambda: "2026-2027")
    monkeypatch.setattr(pipeline, "missing_seasons", lambda: ["2023-2024", "2026-2027"])
    monkeypatch.setattr("sys.argv", ["pipeline", "--auto", "--export"])
    pipeline.main()
    assert calls[0] == ("ingest", ["2023-2024", "2026-2027"])
    assert calls[-1] == ("export",)


def test_auto_mode_refreshes_only_the_current_season_when_nothing_is_missing(monkeypatch):
    calls = record_steps(monkeypatch)
    monkeypatch.setattr(pipeline.ingest, "current_season", lambda: "2026-2027")
    monkeypatch.setattr(pipeline, "missing_seasons", lambda: [])
    monkeypatch.setattr("sys.argv", ["pipeline", "--auto"])
    pipeline.main()
    assert calls[0] == ("ingest", ["2026-2027"])
