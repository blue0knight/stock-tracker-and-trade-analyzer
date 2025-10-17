from __future__ import annotations

import pytest

from src.core.pick_utils import compute_pick_score
from src.systems import system2


def test_compute_pick_score_basic():
    norm = {"gap": 10.0, "rvol": 2.0, "atr": 1.0}
    # Using default weights 0.5, 0.3, 0.2 -> score = 0.5*10 + 0.3*2 + 0.2*1 = 5 + 0.6 + 0.2 = 5.8
    s = compute_pick_score(norm)
    assert abs(s - 5.8) < 1e-6


def _make_candidate(ticker: str, last_price: float, intraday_volume: int, rvol: float, alert_bonus: float = 0.0):
    return {
        "ticker": ticker,
        "last_price": last_price,
        "intraday_volume": intraday_volume,
        "rvol": rvol,
        "gap_pct": 5.0,
        "atr_stretch": 1.0,
        "alert_bonus": alert_bonus,
    }


def test_generate_picks_filtering_and_structure():
    cfg = {"system2": {"max_picks": 3, "min_price": 2.0, "min_intraday_volume": 100, "min_rvol": 0.5, "priority_alerts_only": False, "min_score": 0.0}}
    # Build candidate universe (some fail filters)
    candidates = [
        _make_candidate("AAA", 1.5, 500, 1.0),  # price too low
        _make_candidate("BBB", 2.5, 50, 1.2),   # volume too low
        _make_candidate("CCC", 3.0, 200, 0.3),  # rvol too low
        _make_candidate("DDD", 4.0, 300, 1.5),  # should pass
        _make_candidate("EEE", 5.0, 400, 2.0),  # should pass
    ]
    ctx = {"enriched": candidates, "system1_alerts": [], "dry_run": True}

    picks = system2.generate_picks({"system2": cfg["system2"]}, None, ctx)
    # Expect picks to be list and contain only DDD and EEE (order by pick_score)
    assert isinstance(picks, list)
    assert all(isinstance(p, dict) for p in picks)
    tickers = [p["ticker"] for p in picks]
    assert "DDD" in tickers and "EEE" in tickers
    # Ensure pick_score present
    assert all("pick_score" in p for p in picks)


def test_generate_picks_priority_alerts_only():
    cfg = {"system2": {"max_picks": 5, "min_price": 1.0, "min_intraday_volume": 0, "min_rvol": 0.0, "priority_alerts_only": True}}
    # Candidate without alert_bonus should be excluded
    candidates = [
        _make_candidate("NOP", 10.0, 1000, 1.0, alert_bonus=0.0),
        _make_candidate("ALR", 12.0, 2000, 1.5, alert_bonus=1.0),
    ]
    ctx = {"enriched": candidates, "system1_alerts": [], "dry_run": True}

    picks = system2.generate_picks({"system2": cfg["system2"]}, None, ctx)
    tickers = [p["ticker"] for p in picks]
    assert "ALR" in tickers and "NOP" not in tickers
