"""System1 (Explosive Alerts)

Additive, dry-run friendly implementation scaffold:
 - Detect explosive pre-market bursts by volume + price delta
 - Expose run_system1_scan(context, sim_state=None) -> List[alert_dict]

No network or external side-effects in this scaffold.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
import logging

# Use local scoring helpers (guarded import)
try:
    from src.core import alert_scoring as scoring
except Exception:
    scoring = None

logger = logging.getLogger(__name__)


@dataclass
class ExplosiveAlertConfig:
    enabled: bool = False
    premarket_window_mins: int = 30
    lookback_minutes: int = 5
    volume_multiplier_threshold: float = 3.0
    price_change_pct_threshold: float = 1.5
    min_avg_volume: int = 1000
    alert_window_mins: int = 5
    alert_action: str = "log"


def _load_config(cfg: dict) -> ExplosiveAlertConfig:
    """Validate and return ExplosiveAlertConfig; fallback to defaults on missing keys."""
    c = ExplosiveAlertConfig()
    if not cfg:
        return c
    for k, v in cfg.items():
        if hasattr(c, k):
            setattr(c, k, v)
    return c


def score_burst(current_vol: float, avg_vol: float, first_price: float, last_price: float) -> float:
    """Compute a simple score using core.alert_scoring when available, else fallback heuristics."""
    if scoring:
        vol_mult = scoring.volume_multiplier(current_vol, avg_vol)
        pct = scoring.pct_change(first_price, last_price)
        return scoring.combined_score(vol_mult, pct)
    # fallback
    vol_mult = (current_vol / avg_vol) if (avg_vol and avg_vol > 0) else 0.0
    pct = ((last_price - first_price) / first_price * 100.0) if first_price else 0.0
    return 0.6 * vol_mult + 0.4 * (abs(pct) / 100.0)


def detect_explosive_events(series: Sequence[Dict], cfg: ExplosiveAlertConfig) -> List[Dict]:
    """
    series: time-ordered list of dicts: {symbol, timestamp, price, volume, avg_volume}
    Returns list of alerts: {symbol, timestamp, score, vol_mult, price_pct, reason}
    """
    alerts: List[Dict] = []
    if not series or len(series) < 2:
        return alerts
    # Assume series is for a single symbol in this scaffold
    symbol = series[0].get("symbol")
    volumes = [s.get("volume", 0) for s in series]
    prices = [s.get("price", 0.0) for s in series]
    avg_vol = series[-1].get("avg_volume") or cfg.min_avg_volume
    current_vol = sum(volumes)
    first_price = prices[0]
    last_price = prices[-1]
    vol_mult = (current_vol / avg_vol) if avg_vol and avg_vol > 0 else 0.0
    pct = ((last_price - first_price) / first_price * 100.0) if first_price else 0.0
    score = score_burst(current_vol, avg_vol, first_price, last_price)
    if vol_mult >= cfg.volume_multiplier_threshold and abs(pct) >= cfg.price_change_pct_threshold:
        alerts.append({
            "symbol": symbol,
            "timestamp": series[-1].get("timestamp"),
            "score": score,
            "vol_mult": vol_mult,
            "price_pct": pct,
            "reason": "volume_and_price",
        })
    return alerts


def run_system1_scan(context: dict, sim_state: Optional[object] = None) -> List[Dict]:
    """
    Public entrypoint for scanner to call.
    context expected keys:
     - 'config': dict (systems.system1)
     - 'ticker_data': dict of symbol -> list of snapshots (time-ordered)
     - 'now': datetime for current scan (optional; sim_state may override)
    """
    cfg_dict = (context.get("config") or {}).get("system1", {})
    cfg = _load_config(cfg_dict)
    if not cfg.enabled:
        logger.debug("System1 disabled in config.")
        return []
    ticker_data = context.get("ticker_data", {})
    alerts: List[Dict] = []
    for symbol, series in ticker_data.items():
        series_sorted = sorted(series, key=lambda s: s.get("timestamp"))
        detected = detect_explosive_events(series_sorted, cfg)
        for a in detected:
            a["symbol"] = symbol
            alerts.append(a)
    logger.debug("System1: %d alerts detected", len(alerts))
    return alerts
