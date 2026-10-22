from types import SimpleNamespace

import duckdb
import pytest

from src import clean, model, recap

BRONZE_DDL = """
    CREATE TABLE bronze_events (
        idEvent VARCHAR, strSeason VARCHAR, intRound VARCHAR,
        strHomeTeam VARCHAR, strAwayTeam VARCHAR,
        intHomeScore VARCHAR, intAwayScore VARCHAR,
        dateEvent VARCHAR, strStatus VARCHAR, _loaded_at TIMESTAMP
    )
"""

MATCHES = [
    ("1", "Kaizer Chiefs", "Orlando Pirates", "2", "0", "2025-08-01"),
    ("2", "Orlando Pirates", "Mamelodi Sundowns", "1", "1", "2025-08-08"),
    ("3", "Mamelodi Sundowns", "Kaizer Chiefs", "0", "3", "2025-08-15"),
    ("4", "Kaizer Chiefs", "Mamelodi Sundowns", None, None, "2099-01-01"),  # upcoming
]


class FakeClient:
    """Stands in for anthropic.Anthropic so no real API call is ever made in tests."""

    def __init__(self, text="Chiefs lead the pack."):
        self.calls = []
        self._text = text
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._text)])


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(BRONZE_DDL)
    for event_id, home, away, hg, ag, d in MATCHES:
        c.execute(
            "INSERT INTO bronze_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            [event_id, "2025-2026", "1", home, away, hg, ag, d, "FT", "2026-01-01 10:00:00"],
        )
    clean.build_silver(c)
    model.build_gold(c)
    return c


def test_facts_contain_the_table_leader_and_the_next_fixture(con):
    season, facts = recap.build_facts(con)
    assert season == "2025-2026"
    assert "1. Kaizer Chiefs" in facts
    assert "Kaizer Chiefs v Mamelodi Sundowns" in facts


def test_recap_is_requested_from_the_given_model_and_saved_with_its_facts(con, monkeypatch):
    monkeypatch.setenv("PSL_RECAP_MODEL", "my-test-model")
    client = FakeClient()
    result = recap.generate_recap(con, client)
    call = client.calls[0]
    assert call["model"] == "my-test-model"
    assert "ONLY the facts" in call["system"]
    assert "1. Kaizer Chiefs" in call["messages"][0]["content"]
    saved = con.execute("SELECT model, recap, facts FROM gold_weekly_recap").fetchall()
    assert saved[0][:2] == ("my-test-model", "Chiefs lead the pack.")
    assert "1. Kaizer Chiefs" in saved[0][2]
    assert result["recap"] == "Chiefs lead the pack."


def test_an_empty_answer_is_an_error_and_nothing_is_saved(con):
    with pytest.raises(RuntimeError, match="empty"):
        recap.generate_recap(con, FakeClient(text="   "))
    tables = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'gold_weekly_recap'"
    ).fetchone()[0]
    assert tables == 0


def test_recaps_are_kept_not_overwritten(con):
    recap.generate_recap(con, FakeClient("first"))
    recap.generate_recap(con, FakeClient("second"))
    assert con.execute("SELECT COUNT(*) FROM gold_weekly_recap").fetchone()[0] == 2


def test_rebuilding_gold_does_not_delete_old_recaps(con):
    recap.generate_recap(con, FakeClient("keep me"))
    model.build_gold(con)
    assert con.execute("SELECT recap FROM gold_weekly_recap").fetchall() == [("keep me",)]


def test_run_skips_politely_without_an_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(recap, "make_client", lambda: None)
    assert recap.run(db_path=str(tmp_path / "unused.duckdb")) == {"recap": "skipped"}
    assert not (tmp_path / "unused.duckdb").exists()
