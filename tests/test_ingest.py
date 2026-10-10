from datetime import datetime

import duckdb

from src import ingest


def test_load_bronze_appends_and_keeps_every_load():
    con = duckdb.connect()
    event = {"idEvent": "1", "strHomeTeam": "Amazulu", "strAwayTeam": "Kaizer Chiefs",
             "intHomeScore": "2", "intAwayScore": "1", "strSeason": "2025-2026"}
    ingest.load_bronze(con, [event], datetime(2026, 1, 1))
    ingest.load_bronze(con, [event], datetime(2026, 1, 2))
    # Bronze keeps both copies - de-duplication is silver's job
    assert con.execute("SELECT COUNT(*) FROM bronze_events").fetchone()[0] == 2