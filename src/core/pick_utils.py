from __future__ import annotations

from typing import Dict, List, Optional

"""Utility helpers for ranking and scoring picks (Phase 5).

This file provides a deterministic, testable scoring function and a
simple ranking helper used by System2's pick generator.

The compute_pick_score signature previously had a stray comma; that is
removed here.
"""


def compute_pick_score(norm: Dict, weight_gap: float = 0.5, weight_rvol: float = 0.3, weight_atr: float = 0.2) -> float:
    """Compute a simple weighted score for a normalized candidate.

    Args:
        norm: Dictionary with normalized features (gap, rvol, atr).
        weight_gap: Weight for gap feature.
        weight_rvol: Weight for relative volume feature.
        weight_atr: Weight for ATR/stretch feature.

    Returns:
        float: Weighted aggregate score.
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


def _rank_candidates(candidates: List[Dict], top_n: int = 5) -> List[Dict]:
    """Rank candidates by computed pick score and return top_n entries.

    This helper injects a deterministic "pick_score" field into each
    returned candidate. If a candidate carries an alert-derived bonus,
    it is added as `alert_bonus` to the score calculation.
    """
    scored: List[Dict] = []
    for c in candidates or []:
        # Expect either a pre-normalized dict under 'norm' or fallback to top-level keys
        norm = c.get("norm") or {
            "gap": c.get("gap_pct") or c.get("gap") or 0.0,
            "rvol": c.get("rvol") or 0.0,
            "atr": c.get("atr_stretch") or c.get("atr") or 0.0,
        }

        base_score = compute_pick_score(norm)

        # Optional alert bonus provided by upstream systems (e.g., system1)
        # Use explicit name `alert_bonus` for clarity.
        alert_bonus = 0.0
        try:
            alert_bonus = float(c.get("alert_bonus", 0.0) or 0.0)
        except Exception:
            alert_bonus = 0.0

        # Final score is deterministic and stable for sorting
        final_score = float(base_score + alert_bonus)

        r = dict(c)
        r["pick_score"] = final_score
        r["_scoring_components"] = {"base": base_score, "alert_bonus": alert_bonus}
        scored.append(r)

    scored.sort(key=lambda x: x.get("pick_score", 0.0), reverse=True)
    return scored[:top_n]
