from __future__ import annotations

from typing import Dict, List, Optional, Any

"""
Utility helpers for ranking and scoring picks (Phase 5).

Determinism guarantees:
- compute_pick_score uses fixed weights (gap=0.5, rvol=0.3, atr=0.2 by default).
- _rank_candidates rounds float scores to 6 decimal places and uses a stable
  multi-key tie-breaker: pick_score (desc), alert_bonus (desc), ticker (asc uppercase),
  then last_updated/ts (asc) if present.
"""


def compute_pick_score(norm: Dict[str, Any], weight_gap: float = 0.5, weight_rvol: float = 0.3, weight_atr: float = 0.2) -> float:
    """Compute a deterministic weighted score for a normalized candidate.

    Args:
        norm: Dictionary with normalized features (gap, rvol, atr).
        weight_gap: Weight for gap feature (default 0.5).
        weight_rvol: Weight for relative volume feature (default 0.3).
        weight_atr: Weight for ATR/stretch feature (default 0.2).

    Returns:
        float: Weighted aggregate score (not rounded here; rounding applied in ranking).
    """
    try:
        gap = float(norm.get("gap", 0.0))
    except Exception:
        gap = 0.0
    try:
        rvol = float(norm.get("rvol", 0.0))
    except Exception:
        rvol = 0.0
    try:
        atr = float(norm.get("atr", 0.0))
    except Exception:
        atr = 0.0

    score = (weight_gap * gap) + (weight_rvol * rvol) + (weight_atr * atr)
    return float(score)


def _rank_candidates(candidates: List[Dict[str, Any]], top_n: int = 5) -> List[Dict[str, Any]]:
    """Rank candidates deterministically and return top_n entries.

    Details:
    - For each candidate compute base_score via `compute_pick_score(norm)`.
    - alert_bonus is extracted from candidate (default 0.0).
    - The final pick_score is base_score + alert_bonus, rounded to 6 decimals
      to avoid tiny floating jitter.
    - Sorting order:
       1. pick_score (desc)
       2. alert_bonus (desc)
       3. ticker (asc, uppercase)
       4. last_updated or ts (asc) if present (ISO or numeric)
    - The function injects `pick_score` (rounded) and `_scoring_components` into each
      returned candidate record to aid downstream inspection.
    """
    scored: List[Dict[str, Any]] = []
    for c in candidates or []:
        norm = c.get("norm") or {
            "gap": c.get("gap_pct") or c.get("gap") or 0.0,
            "rvol": c.get("rvol") or 0.0,
            "atr": c.get("atr_stretch") or c.get("atr") or 0.0,
        }

        base_score = compute_pick_score(norm)

        try:
            alert_bonus = float(c.get("alert_bonus", 0.0) or 0.0)
        except Exception:
            alert_bonus = 0.0

        raw_score = base_score + alert_bonus
        # Round to 6 decimals for deterministic ordering
        rounded_score = round(float(raw_score), 6)

        r = dict(c)
        r["pick_score"] = rounded_score
        r["_scoring_components"] = {"base": base_score, "alert_bonus": alert_bonus}
        scored.append(r)

    # Stable deterministic sort with multiple tie-breakers
    def sort_key(x: Dict[str, Any]):
        # pick_score desc -> sort by negative (so higher values come first)
        pk = float(x.get("pick_score", 0.0))
        ab = float(x.get("alert_bonus", 0.0))
        ticker = (x.get("ticker") or "").upper()
        # last_updated/ts may be string or numeric; None sorts after present values
        ts = x.get("last_updated") or x.get("ts") or None
        # Normalize ts for ordering: if numeric keep numeric, else keep as string
        return (-pk, -ab, ticker, ts if ts is not None else "")

    scored.sort(key=sort_key)
    return scored[:top_n]
