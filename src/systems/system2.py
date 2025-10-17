from __future__ import annotations

from typing import List, Dict, Optional, Any

from src.core.pick_utils import _rank_candidates

"""
System2: Pick Generator (Phase 5)

This module implements a deterministic, defensive pick generator for System2.
Key rules:
- Safe casting: invalid rows are skipped
- Deterministic alert bonus: fixed ADDITIVE bonus when a ticker matches an alert
- Config-driven gating (min_price, min_rvol, min_intraday_volume, priority_alerts_only)
"""

ALERT_BONUS_FIXED = 0.1  # deterministic, phase-locked


def generate_picks(cfg: Dict[str, Any], sim_state: Optional[object], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate deterministic picks from enriched candidates.

    Args:
        cfg: systems configuration dict
        sim_state: unused simulation state placeholder
        ctx: context with keys 'enriched' (list) and optional 'system1_alerts' (list)

    Returns:
        list of pick dicts (each enriched with 'pick_score' and optional 'alert_bonus')
    """
    systems_cfg = cfg or {}
    s2 = systems_cfg.get("system2", {})
    top_n = int(s2.get("max_picks", s2.get("top_n", 5)))
    min_price = float(s2.get("min_price", 1.0))
    min_intraday_volume = int(s2.get("min_intraday_volume", 0))
    min_rvol = float(s2.get("min_rvol", 0.0))
    min_score = float(s2.get("min_score", 0.0))
    priority_alerts_only = bool(s2.get("priority_alerts_only", False))

    candidates = list(ctx.get("enriched") or [])

    # Build alert_map (uppercase ticker -> alert dict)
    alert_map: Dict[str, Dict[str, Any]] = {}
    for a in (ctx.get("system1_alerts") or []):
        sym = a.get("symbol") or a.get("ticker") or a.get("symbol_ticker")
        if not sym:
            continue
        alert_map[str(sym).upper()] = a

    # Inject deterministic alert_bonus for matches
    for c in candidates:
        try:
            t = (c.get("ticker") or c.get("symbol") or "").upper()
        except Exception:
            t = ""
        if t and t in alert_map:
            c["alert_bonus"] = ALERT_BONUS_FIXED

    # Filtering with safe casts
    filtered: List[Dict[str, Any]] = []
    for c in candidates:
        # price
        try:
            price = float(c.get("last_price") or c.get("price") or c.get("close"))
        except Exception:
            continue
        if price < min_price:
            continue

        # intraday volume
        try:
            intraday_vol = int(c.get("intraday_volume") or c.get("intraday_vol") or c.get("volume") or 0)
        except Exception:
            continue
        if intraday_vol < min_intraday_volume:
            continue

        # rvol
        try:
            rvol = float(c.get("rvol"))
        except Exception:
            continue
        if rvol < min_rvol:
            continue

        if priority_alerts_only and float(c.get("alert_bonus", 0.0)) == 0.0:
            continue

        filtered.append(c)

    # Rank deterministically using shared helper (which performs rounding and tie-breaks)
    ranked = _rank_candidates(filtered, top_n=top_n)

    # Enforce min_score after ranking (pick_score is present on each ranked item)
    final = [r for r in ranked if float(r.get("pick_score", 0.0)) >= min_score]

    return final


def get_current_picks(state: str) -> List[Dict[str, Any]]:
    """Legacy stub kept for compatibility with scanner dry-run flows."""
    return []


def validate_system() -> bool:
    """Lightweight validator used by scanner.validate_systems() to sanity-check presence."""
    return True