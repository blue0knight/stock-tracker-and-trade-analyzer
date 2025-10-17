from src.core.pick_utils import _rank_candidates


def test_tie_break_ordering():
    # Two candidates with identical numeric scores; ticker should decide deterministic order
    a = {"ticker": "AAA", "pick_score": 5.0, "alert_bonus": 0.0}
    b = {"ticker": "BBB", "pick_score": 5.0, "alert_bonus": 0.0}
    ranked = _rank_candidates([b, a], top_n=2)
    # Ensure ascending ticker tie-break deterministic -> AAA then BBB
    assert ranked[0]["ticker"] == "AAA"
    assert ranked[1]["ticker"] == "BBB"
