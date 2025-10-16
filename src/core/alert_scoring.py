"""
Lightweight scoring helpers for explosive-alert detection (pure math, no side-effects).

Functions:
 - volume_multiplier(current_vol, avg_vol) -> float
 - pct_change(old_price, new_price) -> float
 - combined_score(vol_mult, pct_change, weight_vol=0.6, weight_price=0.4) -> float

Designed for deterministic unit tests and to avoid colliding with existing
`src.core.scoring` module which contains higher-level candidate scoring.
"""
from __future__ import annotations

def volume_multiplier(current_vol: float, avg_vol: float) -> float:
    """Return ratio of current volume to average volume. Avoid div-by-zero."""
    if avg_vol is None or avg_vol <= 0:
        return 0.0
    return float(current_vol) / float(avg_vol)

def pct_change(old_price: float, new_price: float) -> float:
    """Return percentage change (new-old)/old * 100. Handles zero old_price."""
    try:
        return (float(new_price) - float(old_price)) / float(old_price) * 100.0
    except Exception:
        return 0.0

def combined_score(vol_mult: float, pct_change: float, weight_vol: float = 0.6, weight_price: float = 0.4) -> float:
    """Weighted score normalized to positive domain; callers decide threshold semantics."""
    return float(weight_vol) * float(vol_mult) + float(weight_price) * (abs(pct_change) / 100.0)
