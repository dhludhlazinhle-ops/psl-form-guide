"""Day 3 - Elo ratings: pure functions with no database code, so they are easy to test."""

START_RATING = 1500.0
K_FACTOR = 20.0           # how far one result can move a rating
HOME_ADVANTAGE = 60.0     # rating points the home side gets when predicting
SEASON_REGRESSION = 0.25  # at each new season, ratings move 25% back toward 1500

HISTORY_COLUMNS = [
    "match_id", "season", "match_date", "team", "opponent", "is_home",
    "rating_before", "rating_after", "expected_score", "actual_score",
]


def expected_home(r_home: float, r_away: float, home_advantage: float = HOME_ADVANTAGE) -> float:
    """Expected score for the home side: 1 = certain win, 0.5 = even, 0 = certain loss."""
    return 1 / (1 + 10 ** ((r_away - (r_home + home_advantage)) / 400))


def update(r_home, r_away, home_goals, away_goals, k=K_FACTOR, home_advantage=HOME_ADVANTAGE):
    """Return (new_home_rating, new_away_rating, expected_home_score)."""
    exp_home = expected_home(r_home, r_away, home_advantage)
    actual_home = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
    change = k * (actual_home - exp_home)
    return r_home + change, r_away - change, exp_home


def regress(ratings: dict[str, float]) -> dict[str, float]:
    """Pull every rating part of the way back toward the average (squads change between seasons)."""
    keep = 1 - SEASON_REGRESSION
    return {team: START_RATING + keep * (r - START_RATING) for team, r in ratings.items()}


def three_way(exp_home: float, draw_rate: float) -> tuple[float, float, float]:
    """Split the expected score into (home win, draw, away win) probabilities.

    A simple heuristic, not a fitted model: draws are most likely when the teams are
    evenly matched and get rarer as the gap grows. By construction
    p_home + 0.5 * p_draw == exp_home, so the Elo expectation is preserved.
    """
    p_draw = draw_rate * 4 * exp_home * (1 - exp_home)
    return exp_home - p_draw / 2, p_draw, (1 - exp_home) - p_draw / 2


def run_elo(matches):
    """Replay matches in order. Each match is
    (match_id, season, match_date, home, away, home_goals, away_goals).

    Returns (history_rows, final_ratings, last_season). history_rows has two rows per
    match (one per team), in the order given by HISTORY_COLUMNS.
    """
    ratings: dict[str, float] = {}
    history = []
    current_season = None

    for match_id, season, match_date, home, away, hg, ag in matches:
        if current_season is not None and season != current_season:
            ratings = regress(ratings)
        current_season = season

        r_home = ratings.get(home, START_RATING)
        r_away = ratings.get(away, START_RATING)
        new_home, new_away, exp_home = update(r_home, r_away, hg, ag)
        ratings[home], ratings[away] = new_home, new_away

        actual_home = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        history.append((match_id, season, match_date, home, away, True,
                        r_home, new_home, exp_home, actual_home))
        history.append((match_id, season, match_date, away, home, False,
                        r_away, new_away, 1 - exp_home, 1 - actual_home))

    return history, ratings, current_season