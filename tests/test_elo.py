import pytest

from src import elo


def test_even_teams_favour_the_home_side():
    assert elo.expected_home(1500, 1500) > 0.5


def test_update_is_zero_sum():
    new_home, new_away, _ = elo.update(1500, 1500, 2, 0)
    assert new_home + new_away == pytest.approx(3000)


def test_winner_gains_and_loser_drops():
    new_home, new_away, _ = elo.update(1500, 1500, 1, 0)
    assert new_home > 1500 > new_away


def test_upset_moves_ratings_more_than_expected_win():
    fav_win, _, _ = elo.update(1600, 1400, 1, 0)
    upset, _, _ = elo.update(1400, 1600, 1, 0)
    assert (upset - 1400) > (fav_win - 1600)


def test_three_way_probabilities_sum_to_one_and_keep_the_expectation():
    p_home, p_draw, p_away = elo.three_way(0.62, 0.27)
    assert p_home + p_draw + p_away == pytest.approx(1.0)
    assert p_home + 0.5 * p_draw == pytest.approx(0.62)
    assert min(p_home, p_draw, p_away) >= 0


def test_run_elo_writes_two_history_rows_per_match_and_conserves_rating():
    matches = [
        (1, "2025-2026", "2025-08-01", "A", "B", 2, 0),
        (2, "2025-2026", "2025-08-08", "B", "C", 1, 1),
        (3, "2025-2026", "2025-08-15", "C", "A", 0, 3),
    ]
    history, ratings, last_season = elo.run_elo(matches)
    assert len(history) == 6
    assert sum(ratings.values()) == pytest.approx(1500 * 3)
    assert last_season == "2025-2026"


def test_ratings_regress_toward_the_mean_at_a_new_season():
    matches = [
        (1, "2024-2025", "2025-05-01", "A", "B", 3, 0),
        (2, "2025-2026", "2025-08-01", "A", "C", 1, 0),
    ]
    history, _, _ = elo.run_elo(matches)
    a_after_season_1 = history[0][7]
    a_before_season_2 = [h for h in history if h[0] == 2 and h[3] == "A"][0][6]
    expected = 1500 + 0.75 * (a_after_season_1 - 1500)
    assert a_before_season_2 == pytest.approx(expected)