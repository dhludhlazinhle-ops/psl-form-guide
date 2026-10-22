from datetime import date, datetime

import duckdb
import pytest

from src import ingest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"events": []}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ingest.requests.HTTPError(f"HTTP {self.status_code}")


def test_load_bronze_appends_and_keeps_every_load():
    con = duckdb.connect()
    event = {"idEvent": "1", "strHomeTeam": "Amazulu", "strAwayTeam": "Kaizer Chiefs",
             "intHomeScore": "2", "intAwayScore": "1", "strSeason": "2025-2026"}
    ingest.load_bronze(con, [event], datetime(2026, 1, 1))
    ingest.load_bronze(con, [event], datetime(2026, 1, 2))
    # Bronze keeps both copies - de-duplication is silver's job
    assert con.execute("SELECT COUNT(*) FROM bronze_events").fetchone()[0] == 2


def test_current_season_rolls_over_in_july():
    assert ingest.current_season(date(2026, 10, 17)) == "2026-2027"
    assert ingest.current_season(date(2027, 3, 1)) == "2026-2027"
    assert ingest.current_season(date(2027, 6, 30)) == "2026-2027"
    assert ingest.current_season(date(2027, 7, 1)) == "2027-2028"


def test_rate_limit_waits_as_long_as_the_api_asks_then_succeeds(monkeypatch):
    responses = iter([
        FakeResponse(429, headers={"Retry-After": "7"}),
        FakeResponse(200, payload={"events": [{"idEvent": "1"}]}),
    ])
    slept = []
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: next(responses))
    monkeypatch.setattr(ingest.time, "sleep", slept.append)
    assert ingest.fetch_round("2025-2026", 1) == [{"idEvent": "1"}]
    assert slept == [7.0]


def test_rate_limit_gives_up_after_the_last_retry(monkeypatch):
    slept = []
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(429))
    monkeypatch.setattr(ingest.time, "sleep", slept.append)
    with pytest.raises(RuntimeError, match="Giving up"):
        ingest.fetch_round("2025-2026", 1, retries=2)
    assert slept == [30, 60]


def test_a_failure_part_way_keeps_the_seasons_already_loaded(monkeypatch, tmp_path):
    def fake_fetch_round(season, rnd, retries=6):
        if season == "2025-2026":
            raise RuntimeError("Giving up")
        return [{"idEvent": "1", "strSeason": season, "strHomeTeam": "Amazulu",
                 "strAwayTeam": "Kaizer Chiefs"}]

    db = str(tmp_path / "psl.duckdb")
    monkeypatch.setattr(ingest, "fetch_round", fake_fetch_round)
    monkeypatch.setattr(ingest, "REQUEST_DELAY", 0)
    monkeypatch.setattr(ingest, "MAX_ROUNDS", 1)
    monkeypatch.setattr(ingest, "DB_PATH", db)
    monkeypatch.setattr(ingest, "BRONZE_DIR", tmp_path / "bronze")

    with pytest.raises(RuntimeError):
        ingest.run(["2024-2025", "2025-2026"])

    con = duckdb.connect(db)
    assert con.execute("SELECT strSeason FROM bronze_events").fetchall() == [("2024-2025",)]