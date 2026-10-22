"""Day 4 - Streamlit dashboard over the gold tables.  Run: streamlit run dashboard/app.py"""
import os

import duckdb
import pandas as pd
import streamlit as st

DB_PATH = os.environ.get("PSL_DB_PATH", "data/psl.duckdb")
FORM_ICONS = {"W": "🟩", "D": "🟨", "L": "🟥"}

st.set_page_config(page_title="PSL Form Guide", page_icon="⚽", layout="wide")


@st.cache_data(ttl=300)
def query(db_path: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a read-only query. Cached for 5 minutes so clicking around stays fast."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(sql, list(params)).df()
    finally:
        con.close()


if not os.path.exists(DB_PATH):
    st.error(f"No database found at {DB_PATH}. Run `python -m src.pipeline --all` first.")
    st.stop()

st.title("⚽ PSL Form Guide")

meta = query(DB_PATH, """
    SELECT (SELECT max(match_date) FROM silver_matches WHERE is_played) AS last_result,
           (SELECT max(_loaded_at) FROM bronze_events) AS last_load
""").iloc[0]
last_result = pd.to_datetime(meta["last_result"])
last_load = pd.to_datetime(meta["last_load"])
st.caption(
    f"Latest result in the data: {last_result:%d %b %Y} · "
    f"last data refresh: {last_load:%d %b %Y %H:%M} UTC"
)

tab_table, tab_elo, tab_form, tab_pred, tab_recap = st.tabs(
    ["League table", "Elo ratings", "Form guide", "Predictions", "Weekly recap"]
)

with tab_table:
    seasons = query(
        DB_PATH, "SELECT DISTINCT season FROM gold_league_table ORDER BY season DESC"
    )["season"].tolist()
    season = st.selectbox("Season", seasons)
    table = query(DB_PATH, """
        SELECT position AS "#", team_name AS team, played, won, drawn, lost,
               goals_for AS gf, goals_against AS ga, goal_diff AS gd, points AS pts
        FROM gold_league_table WHERE season = ? ORDER BY position
    """, (season,))
    st.dataframe(table, hide_index=True)

with tab_elo:
    current = query(DB_PATH, """
        SELECT team_name AS team, elo, last_match_date AS last_match
        FROM gold_elo_current WHERE is_active ORDER BY elo DESC
    """)
    st.caption(
        "Elo is a strength rating: beating strong teams earns more points, "
        "and everyone starts at 1500. Ratings drift back toward 1500 each season."
    )
    picked = st.multiselect(
        "Teams to chart", current["team"].tolist(), default=current["team"].head(5).tolist()
    )
    if picked:
        history = query(DB_PATH, "SELECT match_date, team, rating_after FROM gold_elo_history")
        history = history[history["team"].isin(picked)]
        wide = history.pivot_table(
            index="match_date", columns="team", values="rating_after", aggfunc="last"
        ).ffill()
        st.line_chart(wide)
    st.dataframe(current, hide_index=True)

with tab_form:
    form = query(DB_PATH, """
        SELECT team_name AS team, form_last5, points_last5 AS pts
        FROM gold_form WHERE is_active ORDER BY points_last5 DESC, team_name
    """)
    form["last 5"] = form["form_last5"].map(lambda s: " ".join(FORM_ICONS[c] for c in s))
    st.caption("Last five results, oldest on the left and newest on the right.")
    st.dataframe(form[["team", "last 5", "pts"]], hide_index=True)

with tab_pred:
    preds = query(DB_PATH, """
        SELECT match_date AS date, home_team, away_team,
               round(p_home * 100, 1) AS home_win,
               round(p_draw * 100, 1) AS draw,
               round(p_away * 100, 1) AS away_win
        FROM gold_fixture_predictions ORDER BY match_date, match_id
    """)
    if preds.empty:
        st.info("No upcoming fixtures in the data yet. Refresh with `python -m src.pipeline`.")
    else:
        preds["date"] = pd.to_datetime(preds["date"]).dt.date
        teams = sorted(set(preds["home_team"]) | set(preds["away_team"]))
        choice = st.selectbox("Filter by team", ["All teams"] + teams)
        if choice != "All teams":
            preds = preds[(preds["home_team"] == choice) | (preds["away_team"] == choice)]
        bar = lambda label: st.column_config.ProgressColumn(  # noqa: E731
            label, min_value=0, max_value=100, format="%.0f"
        )
        st.dataframe(
            preds,
            hide_index=True,
            column_config={
                "home_win": bar("Home win %"),
                "draw": bar("Draw %"),
                "away_win": bar("Away win %"),
            },
        )
    st.caption(
        "Probabilities come from Elo ratings plus a simple draw heuristic. "
        "They are estimates, not betting advice."
    )

with tab_recap:
    has_recap_table = query(DB_PATH, """
        SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = 'gold_weekly_recap'
    """).iloc[0]["n"]
    latest = (
        query(DB_PATH, """
            SELECT generated_at, season, model, recap, facts
            FROM gold_weekly_recap ORDER BY generated_at DESC LIMIT 1
        """)
        if has_recap_table
        else pd.DataFrame()
    )
    if latest.empty:
        st.info("No recap yet. Set ANTHROPIC_API_KEY, then run `python -m src.recap`.")
    else:
        row = latest.iloc[0]
        st.markdown(row["recap"])
        st.caption(
            f"Written by {row['model']} on {pd.to_datetime(row['generated_at']):%d %b %Y %H:%M} UTC, "
            "using only the numbers from the other tabs."
        )
        with st.expander("Facts the recap was written from"):
            st.text(row["facts"])
