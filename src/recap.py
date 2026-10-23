"""Day 5 - Weekly recap: turn the gold tables into a short pundit-style summary with Claude.

python -m src.recap      needs ANTHROPIC_API_KEY (in your environment or a .env file)
"""
import os
from datetime import datetime, timezone

import duckdb

try:  # a .env file is optional; python-dotenv just loads it if it exists
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DB_PATH = "data/psl.duckdb"
DEFAULT_MODEL = "claude-sonnet-5-5"  # override with the PSL_RECAP_MODEL environment variable
PLACEHOLDER_KEYS = {"", "your-key-here"}  # what .env.example ships with

SYSTEM_PROMPT = (
    "You are a witty South African football pundit writing a short weekly PSL recap. "
    "Use ONLY the facts provided and never invent scores, injuries, quotes or transfers. "
    "Write 120 to 180 words in plain, punchy English with a light Mzansi flavour. "
    "Cover the top of the table, one team in form, one team struggling, and one fixture "
    "to watch, quoting the win probabilities you were given. "
    "Finish with one short line saying the probabilities are Elo-based estimates."
)


def build_facts(con, n_fixtures: int = 3) -> tuple[str, str]:
    """Collect the numbers the recap may talk about. Returns (season, facts_text)."""
    season = con.execute("SELECT max(season) FROM gold_league_table").fetchone()[0]
    table = con.execute("""
        SELECT position, team_name, played, won, drawn, lost, goal_diff, points
        FROM gold_league_table WHERE season = ? ORDER BY position
    """, [season]).fetchall()
    form = con.execute("""
        SELECT team_name, form_last5, points_last5 FROM gold_form
        WHERE is_active ORDER BY points_last5 DESC, team_name
    """).fetchall()
    elo = con.execute("""
        SELECT team_name, elo FROM gold_elo_current WHERE is_active ORDER BY elo DESC LIMIT 5
    """).fetchall()
    fixtures = con.execute("""
        SELECT match_date, home_team, away_team, p_home, p_draw, p_away
        FROM gold_fixture_predictions ORDER BY match_date, match_id LIMIT ?
    """, [n_fixtures]).fetchall()

    def row(r):
        return f"{r[0]}. {r[1]} - {r[7]} pts (P{r[2]} W{r[3]} D{r[4]} L{r[5]}, GD {r[6]:+d})"

    lines = [f"Season: {season}", "", "Top of the table:"]
    lines += [row(r) for r in table[:5]]
    lines += ["", "Bottom of the table:"]
    lines += [row(r) for r in table[-3:]]
    lines += ["", "Form over the last 5 matches (oldest to newest):"]
    lines += [f"{t}: {f} ({p} pts)" for t, f, p in form[:3]]
    lines += [f"{t}: {f} ({p} pts)  <- struggling" for t, f, p in form[-2:]]
    lines += ["", "Highest Elo ratings (1500 is average):"]
    lines += [f"{t}: {e:.0f}" for t, e in elo]
    if fixtures:
        lines += ["", "Next fixtures with win probabilities:"]
        lines += [
            f"{d:%d %b}: {h} v {a} - home {ph:.0%}, draw {pd_:.0%}, away {pa:.0%}"
            for d, h, a, ph, pd_, pa in fixtures
        ]
    return season, "\n".join(lines)


def save_recap(con, season: str, model: str, facts: str, text: str) -> None:
    """Recaps are kept (append-only), together with the facts they were written from."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS gold_weekly_recap (
            generated_at TIMESTAMP, season VARCHAR, model VARCHAR, facts VARCHAR, recap VARCHAR
        )
    """)
    generated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    con.execute(
        "INSERT INTO gold_weekly_recap VALUES (?, ?, ?, ?, ?)",
        [generated_at, season, model, facts, text],
    )


def generate_recap(con, client, model: str | None = None) -> dict:
    model = model or os.environ.get("PSL_RECAP_MODEL", DEFAULT_MODEL)
    season, facts = build_facts(con)
    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": facts}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "text") == "text"
    ).strip()
    if not text:
        raise RuntimeError("Claude returned an empty recap")
    save_recap(con, season, model, facts, text)
    return {"season": season, "model": model, "recap": text}


def make_client():
    """Return an Anthropic client, or None when no real API key is configured."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key in PLACEHOLDER_KEYS:
        return None
    import anthropic  # imported here so the rest of the project works without it

    return anthropic.Anthropic(api_key=key)


def _api_error_types() -> tuple:
    """The exceptions that mean 'the API call failed' (bad key, no credit, network, ...)."""
    try:
        import anthropic

        return (anthropic.APIError,)
    except (ImportError, AttributeError):
        return ()


def run(db_path: str = DB_PATH, client=None) -> dict:
    client = client or make_client()
    if client is None:
        print("ANTHROPIC_API_KEY is not set (or is still the placeholder in .env) - "
              "skipping the weekly recap")
        return {"recap": "skipped"}
    con = duckdb.connect(db_path)
    try:
        result = generate_recap(con, client)
    except _api_error_types() as exc:
        print(f"Weekly recap failed ({type(exc).__name__}): {exc}\n"
              "Check ANTHROPIC_API_KEY in your .env file and that your account has credit.")
        return {"recap": "failed"}
    finally:
        con.close()
    print(result["recap"])
    return {"recap": "generated"}


def main() -> None:
    run()


if __name__ == "__main__":
    main()