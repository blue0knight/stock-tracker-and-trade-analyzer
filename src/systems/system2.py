from __future__ import annotations

from typing import List, Dict, Optional

from src.core.pick_utils import _rank_candidates

"""System2: Pick Generator (Phase 5)

Simple, deterministic pick generator that ranks enriched candidates and
optionally applies alert-derived bonuses from other systems.
"""


def generate_picks(cfg: dict, sim_state: Optional[object], ctx: dict) -> List[Dict]:
    """Generate picks from enriched candidates.

    Args:
        cfg: systems configuration dict (whole scanner systems block recommended)
        sim_state: optional SimulationState instance (unused here but accepted)
        ctx: runtime context containing 'enriched' (list of candidate dicts)

    Returns:
        List[Dict]: list of pick dicts (top_n) with `pick_score` inserted.
    """
    systems_cfg = cfg or {}
    s2 = systems_cfg.get("system2", {})
    # Support both 'max_picks' and legacy 'top_n'
    top_n = int(s2.get("max_picks", s2.get("top_n", 5)))
    min_price = float(s2.get("min_price", 1.0))
    min_intraday_volume = int(s2.get("min_intraday_volume", 0))
    min_rvol = float(s2.get("min_rvol", 0.0))
    min_score = float(s2.get("min_score", 0.0))
    priority_alerts_only = bool(s2.get("priority_alerts_only", False))

    candidates = list(ctx.get("enriched") or [])

    # Apply simple alert bonus: if ctx has system1_alerts mapping, boost matching tickers
    alert_map = {}
    for a in ctx.get("system1_alerts") or []:
        t = a.get("ticker")
        if not t:
            continue
        # Use a small deterministic bonus based on alert score if present
        try:
            alert_map[t.upper()] = float(a.get("score", 1.0)) * 0.5
        except Exception:
            alert_map[t.upper()] = 0.5

    # Inject alert_bonus into candidates where applicable
    for c in candidates:
        t = (c.get("ticker") or "").upper()
        if t in alert_map:
            c["alert_bonus"] = alert_map[t]

    # Apply configurable filtering (min_price, min_intraday_volume, min_rvol, priority_alerts_only)
    filtered: List[Dict] = []
    for c in candidates:
        try:
            price = float(c.get("last_price") or c.get("price") or c.get("close") or 0.0)
        except Exception:
            price = 0.0
        if price < min_price:
            continue

        try:
            intraday_vol = int(c.get("intraday_volume") or c.get("intraday_vol") or c.get("volume") or 0)
        except Exception:
            intraday_vol = 0
        if intraday_vol < min_intraday_volume:
            continue

        try:
            rvol = float(c.get("rvol") or 0.0)
        except Exception:
            rvol = 0.0
        if rvol < min_rvol:
            continue

        if priority_alerts_only and float(c.get("alert_bonus", 0.0)) == 0.0:
            continue

        filtered.append(c)

    ranked = _rank_candidates(filtered, top_n=top_n)

    # Filter by min_score
    final = [r for r in ranked if float(r.get("pick_score", 0.0)) >= min_score]

    return final


def get_current_picks(state: str) -> List[Dict]:
    """Backwards-compatible stub for scanner.get_picks_from_systems.

    This is intentionally minimal; real invocation should use `generate_picks` via
    integration point in scanner with full cfg/sim_state/context.
    """
    return []


def validate_system() -> bool:
    """Basic validation helper used by scanner.validate_systems()."""
    # No external deps; always valid
    return True
"""System2 module for Phase 2 implementation.

Provides a dry-run compatible stub for System2 operations.
"""
import logging

logger = logging.getLogger(__name__)

def get_current_picks(state: str) -> list:
    """Get current picks for System2 based on market state.
    
    Args:
        state: Current market state (e.g. PREMARKET)
        
    Returns:
        List of picks (empty during dry-run)
    """
    logger.info("System2: Processing picks for state %s", state)
    # Dry-run stub - no external calls
    return []

def validate_system() -> bool:
    """Validate System2 configuration and dependencies.
    
    Returns:
        True if system is valid
    """
    logger.info("System2: Validating configuration")
    return True