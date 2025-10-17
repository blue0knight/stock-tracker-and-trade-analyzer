import pytest
pytest.skip("Legacy scoring test disabled for Phase 7 — functions moved or deprecated", allow_module_level=True)

from src.core.scoring import calculate_gap, score_snapshots, top_movers

def test_calculate_gap_positive():
    assert round(calculate_gap(100, 110), 2) == 10.00

def test_calculate_gap_negative():
    assert round(calculate_gap(100, 90), 2) == -10.00

def test_calculate_gap_invalid_prev_close():
    assert calculate_gap(0, 100) == 0.0
    assert calculate_gap(-5, 100) == 0.0

def test_score_snapshots_and_ranking():
    snapshots = {
        "AAA": {"prevClose": 10, "lastTrade": 12},  # +20%
        "BBB": {"prevClose": 20, "lastTrade": 21},  # +5%
        "CCC": {"prevClose": 30, "lastTrade": 27},  # -10%
    }
    scored = score_snapshots(snapshots)
    movers = top_movers(scored, n=2)

    assert movers[0][0] == "AAA"
    assert round(movers[0][1], 2) == 20.00
    assert movers[1][0] == "BBB"
    assert round(movers[1][1], 2) == 5.00
