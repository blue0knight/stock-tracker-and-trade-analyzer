from __future__ import annotations

# -------------------------------------------------------------------
# Scanner (final)
#  - Fetch → Enrich (incl. ATR) → Liquidity → Score → Final Pick
#  - Single CSV: today_pick.csv with FinalPick flag + rationale
#  - Debug mode via YAML (no code edits needed for weekends)
# -------------------------------------------------------------------

import os
import sys
import csv
import time
import yaml
import shutil
import logging
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Core + adapters
from src.core.scoring import score_snapshots, log_top_movers
from src.core.output import write_watchlist
from src.adapters import polygon_adapter as pa
from src.adapters.polygon_adapter import fetch_snapshots

# Systems (Phase 2)
from src.systems import system1, system2

# Optional scheduler hook (Phase 1). Kept minimal and reversible.
try:
    # Local import to avoid introducing dependency when not used
    from src.core.scheduler import Scheduler  # type: ignore
except Exception:
    Scheduler = None


# -------------------------------------------------------------------
# Env / Config / Logger
# -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "configs" / "scanner.yaml"
SYSTEMS_CONFIG_PATH = ROOT / "configs" / "systems.yaml"

print(f"DEBUG: Expecting .env at {ENV_PATH}")
load_dotenv(ENV_PATH)

if not os.getenv("POLYGON_API_KEY"):
    raise RuntimeError(f"❌ POLYGON_API_KEY not loaded. Expected in {ENV_PATH}")
print("DEBUG: POLYGON_API_KEY loaded ✓")

def load_systems_config():
    """Load and validate systems configuration.
    
    Returns:
        dict: Systems configuration
    """
    if not SYSTEMS_CONFIG_PATH.exists():
        logger.warning("Systems config not found at %s", SYSTEMS_CONFIG_PATH)
        return {"systems": {"enabled": []}}
        
    with open(SYSTEMS_CONFIG_PATH) as f:
        config = yaml.safe_load(f)
        
    return config

def get_picks_from_systems(state: str):
    """Get picks from all enabled systems for current market state.
    
    Args:
        state: Current market state (PREMARKET, OPEN, etc)
        
    Returns:
        dict: System picks by system name
    """
    systems_config = load_systems_config()
    enabled_systems = systems_config.get("systems", {}).get("enabled", [])
    
    picks = {}
    if "system1" in enabled_systems:
        picks["system1"] = system1.get_current_picks(state)
        
    if "system2" in enabled_systems:
        picks["system2"] = system2.get_current_picks(state)
        
    return picks

def validate_systems():
    """Validate all enabled systems.
    
    Returns:
        bool: True if all systems valid
    """
    systems_config = load_systems_config()
    enabled_systems = systems_config.get("systems", {}).get("enabled", [])
    
    valid = True
    if "system1" in enabled_systems:
        valid &= system1.validate_system()
        
    if "system2" in enabled_systems:
        valid &= system2.validate_system()
        
    return valid


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

def load_group_watchlist() -> list[str]:
    """
    Load group watchlist from CSV file.
    Falls back to empty list if file doesn't exist.
    """
    csv_path = Path("configs/group_watchlist.csv")
    if not csv_path.exists():
        return []

    tickers = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip().upper()
            if ticker:
                tickers.append(ticker)
    return tickers


## -------------------------------------------------------------------
## BLOCK: snapshot-history-tracker  |  FILE: src/scanner/scanner.py  |  DATE: 2025-10-01
## PURPOSE: In-memory rolling history of last 3-5 snapshots per ticker for heartbeat detection
## NOTES:
##   - Stores: {ticker: [(timestamp, price, volume), ...]}
##   - Max 5 snapshots per ticker (FIFO queue)
##   - Used to detect price/volume movement (heartbeat)
## -------------------------------------------------------------------
from collections import deque, defaultdict
import concurrent.futures

# Global snapshot history (in-memory)
SNAPSHOT_HISTORY = defaultdict(lambda: deque(maxlen=5))

# Flag to allow pass-through on the first market scan after history clear
MARKET_OPEN_FIRST_SCAN = True

def record_snapshot(ticker: str, price: float, volume: int, timestamp: datetime = None) -> None:
    """Record a snapshot for heartbeat tracking."""
    if timestamp is None:
        timestamp = datetime.now(pytz.timezone("America/New_York"))
    SNAPSHOT_HISTORY[ticker].append((timestamp, price, volume))

def get_snapshot_history(ticker: str) -> list:
    """Get historical snapshots for a ticker (oldest to newest)."""
    return list(SNAPSHOT_HISTORY[ticker])

def clear_snapshot_history() -> None:
    """Clear all snapshot history (call at start of new trading day)."""
    SNAPSHOT_HISTORY.clear()

## -------------------------------------------------------------------
## BLOCK: market-open-price-tracker  |  FILE: src/scanner/scanner.py  |  DATE: 2025-10-09
## PURPOSE: Track 9:30 AM open prices to calculate intraday movement after market opens
## NOTES:
##   - Stores: {ticker: open_price_at_930}
##   - Used to calculate intraday % change instead of gap vs prev_close
##   - Captured at first scan after 9:30 AM
## -------------------------------------------------------------------
MARKET_OPEN_PRICES = {}

def record_market_open_price(ticker: str, price: float) -> None:
    """Record 9:30 AM open price (only once per ticker per day)."""
    if ticker not in MARKET_OPEN_PRICES:
        MARKET_OPEN_PRICES[ticker] = price

def get_market_open_price(ticker: str) -> float:
    """Get recorded 9:30 AM open price, or None if not recorded."""
    return MARKET_OPEN_PRICES.get(ticker)

def clear_market_open_prices() -> None:
    """Clear all market open prices (call at start of new trading day)."""
    MARKET_OPEN_PRICES.clear()

# Marker to guarantee the first-market pass is used only once per process
MARKET_OPEN_FIRST_SCAN_USED = False

def is_market_open() -> bool:
    """Check if market is currently open (after 9:30 AM)."""
    now = datetime.now(pytz.timezone("America/New_York"))
    return now.hour > 9 or (now.hour == 9 and now.minute >= 30)

## -------------------------------------------------------------------
## BLOCK: heartbeat-detection  |  FILE: src/scanner/scanner.py  |  DATE: 2025-10-03
## PURPOSE: Detect if a ticker is "alive" (actively moving) vs stagnant
## NOTES:
##   - Adaptive window: 30min before 9:30 AM, 5min during 9:30-12:00, 30min after 12:00
##   - Checks: price changed? volume growing? not frozen?
## -------------------------------------------------------------------
def get_scan_cadence_minutes() -> int:
    """
    Return dynamic scan cadence based on time of day.

    Strategy:
    - 4:00-9:00 AM: 30 min (early premarket, slower)
    - 9:00-9:15 AM: 15 min (pre-open ramp up)
    - 9:15-9:30 AM: 5 min (final approach to open)
    - 9:30+ AM: 5 min (market hours)
    """
    now = datetime.now(pytz.timezone("America/New_York"))
    hour, minute = now.hour, now.minute

    # 4:00-9:00 AM: 30 minute cadence
    if hour < 9:
        return 30
    # 9:00-9:15 AM: 15 minute cadence
    elif hour == 9 and minute < 15:
        return 15
    # 9:15+ AM: 5 minute cadence
    else:
        return 5

def get_heartbeat_window_minutes() -> int:
    """
    Return adaptive heartbeat window based on time of day.

    Strategy:
    - Before 9:30: 30min (premarket, slower)
    - 9:30-12:00: 5min (opening volatility, catch fast movers)
    - 12:00-4:00: 30min (afternoon, slower/less volume)
    """
    now = datetime.now(pytz.timezone("America/New_York"))
    hour, minute = now.hour, now.minute

    # Before 9:30 AM: 30 minute window (premarket)
    if hour < 9 or (hour == 9 and minute < 30):
        return 30
    # 9:30 AM - 12:00 PM: 5 minute window (TIGHT - opening volatility)
    elif hour < 12:
        return 5
    # 12:00 PM - 4:00 PM: 30 minute window (RELAXED - afternoon doldrums)
    else:
        return 30

def has_heartbeat(ticker: str, min_price_change_pct: float = 0.5, min_volume_growth: int = 1000) -> tuple[bool, str]:
    """
    Check if ticker has a "heartbeat" (active movement).

    AFTER MARKET OPEN (9:30 AM+):
        Uses (current_price - market_open_price) to detect intraday movers

    BEFORE MARKET OPEN (premarket):
        Uses (current_price - last_scan_price) for relative movement

    Returns:
        (has_heartbeat: bool, reason: str)
    """
    history = get_snapshot_history(ticker)

    # Determine market state defensively
    try:
        now_market_open = is_market_open()
    except Exception:
        now_market_open = False

    # If we have very little history, decide based on session:
    # - Premarket: allow a per-ticker first_scan_pass so we can show something
    # - Market open: only allow a global one-time first_market_pass controlled by
    #   MARKET_OPEN_FIRST_SCAN. After that, insufficient history should fail.
    if len(history) < 2:
        if not now_market_open:
            return True, "first_scan_pass"
        # We're in market hours with insufficient history; allow a one-time
        # global pass-through if the operator requested it on startup.
        # Use an explicit USED flag to prevent any possibility of the
        # global being observed as True across multiple tickers due to
        # unexpected module reloads or race conditions.
        global MARKET_OPEN_FIRST_SCAN_USED
        if MARKET_OPEN_FIRST_SCAN and not MARKET_OPEN_FIRST_SCAN_USED and now_market_open:
            try:
                globals()["MARKET_OPEN_FIRST_SCAN"] = False
            except Exception:
                pass
            try:
                globals()["MARKET_OPEN_FIRST_SCAN_USED"] = True
                MARKET_OPEN_FIRST_SCAN_USED = True
            except Exception:
                pass
            # Diagnostic log to make the one-time pass visible in logs
            try:
                logging.getLogger("scanner").info(f"DBG: has_heartbeat -> issuing first_market_pass for {ticker}")
            except Exception:
                pass
            return True, "first_market_pass"
        return False, f"insufficient_history"

    # Get adaptive window
    window_minutes = get_heartbeat_window_minutes()
    now = datetime.now(pytz.timezone("America/New_York"))
    cutoff_time = now - timedelta(minutes=window_minutes)

    # Filter snapshots within the window
    recent = [s for s in history if s[0] >= cutoff_time]

    if len(recent) < 2:
        # Fallback: if window is tight (5 min) and we have ANY history, use it
        # This handles market open transition when we only have premarket 30-min data
        if window_minutes <= 5 and len(history) >= 2:
            recent = history[-2:]  # Use last 2 snapshots regardless of time
        else:
            return False, f"no_data_in_last_{window_minutes}min"

    # Check 1: Price movement
    # CRITICAL FIX: Use market open price (9:30 AM) as reference if available
    market_open_price = get_market_open_price(ticker)
    newest_price = recent[-1][1]

    if is_market_open() and market_open_price is not None:
        # After 9:30 AM: Calculate change from market open
        reference_price = market_open_price
        reference_label = "open_930"
    else:
        # Before 9:30 AM (premarket): Use oldest in window
        reference_price = recent[0][1]
        reference_label = f"last_{window_minutes}min"

    if reference_price <= 0:
        return False, "invalid_price"

    price_change_pct = (newest_price - reference_price) / reference_price * 100.0  # Signed (can be negative)

    # Check 2: Volume growth
    oldest_volume = recent[0][2]
    newest_volume = recent[-1][2]
    volume_growth = newest_volume - oldest_volume

    # Check 3: Not frozen (price identical for all recent snapshots)
    unique_prices = set(s[1] for s in recent)
    is_frozen = len(unique_prices) == 1

    # Heartbeat criteria (MUST BE UPWARD MOVEMENT)
    if is_frozen:
        return False, "price_frozen"

    # Require UPWARD price movement (filter out downtrending stocks like TSPH)
    if price_change_pct < 0:
        return False, f"downtrending_vs_{reference_label}"

    # Require minimum upward movement OR volume growth
    if price_change_pct < min_price_change_pct and volume_growth < min_volume_growth:
        return False, f"insufficient_movement_vs_{reference_label}"

    # Return success with reference label (shows what price we compared against)
    return True, f"active_vs_{reference_label}"

## -------------------------------------------------------------------
## BLOCK: composite-scoring  |  FILE: src/scanner/scanner.py  |  DATE: 2025-10-01
## PURPOSE: Multi-factor scoring for Top 5 (replaces simple gap% sort)
## FORMULA:
##   score = (gap_pct * decay_weight)
##         + (intraday_delta * delta_weight)
##         + (volume_rate * volume_weight)
##         + (rvol_trend * rvol_weight)
##         + (velocity_bonus if price changed in last 30m)
## -------------------------------------------------------------------
def calculate_gap_decay_factor() -> float:
    """Gap% loses weight as session progresses (10% per hour after open)."""
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)
    market_open = ny.localize(datetime.combine(now.date(), datetime.strptime("09:30", "%H:%M").time()))

    if now < market_open:
        return 1.0  # Full weight premarket

    hours_since_open = (now - market_open).seconds / 3600.0
    decay_factor = max(0.4, 1.0 - (hours_since_open * 0.1))  # Min 40% weight
    return decay_factor

def calculate_intraday_delta(ticker: str, window_minutes: int = 30) -> float:
    """Calculate % price change in last N minutes."""
    history = get_snapshot_history(ticker)
    if len(history) < 2:
        return 0.0

    now = datetime.now(pytz.timezone("America/New_York"))
    cutoff_time = now - timedelta(minutes=window_minutes)

    recent = [s for s in history if s[0] >= cutoff_time]
    if len(recent) < 2:
        return 0.0

    old_price = recent[0][1]
    new_price = recent[-1][1]

    if old_price <= 0:
        return 0.0

    return (new_price - old_price) / old_price * 100.0

def calculate_volume_rate(ticker: str, window_minutes: int = 30) -> float:
    """Calculate shares/minute in last N minutes."""
    history = get_snapshot_history(ticker)
    if len(history) < 2:
        return 0.0

    now = datetime.now(pytz.timezone("America/New_York"))
    cutoff_time = now - timedelta(minutes=window_minutes)

    recent = [s for s in history if s[0] >= cutoff_time]
    if len(recent) < 2:
        return 0.0

    old_volume = recent[0][2]
    new_volume = recent[-1][2]
    volume_delta = new_volume - old_volume

    if volume_delta <= 0:
        return 0.0

    # Shares per minute
    time_elapsed_minutes = (recent[-1][0] - recent[0][0]).seconds / 60.0
    if time_elapsed_minutes <= 0:
        return 0.0

    return volume_delta / time_elapsed_minutes

def calculate_composite_score(ticker: str, gap_pct: float, current_price: float, current_volume: int) -> float:
    """
    Calculate composite score for Top 5 ranking.

    Weights (updated 2025-10-03 to catch high-volume runners like DFLI):
    - gap_pct * decay: 20% (reduced from 25% - gap alone not enough)
    - intraday_delta: 30%
    - volume_score: 30% (increased from 25% - volume matters!)
    - velocity_bonus: 20 points
    """
    # Time decay on gap
    decay_factor = calculate_gap_decay_factor()
    gap_score = gap_pct * decay_factor * 0.20

    # Intraday price movement
    intraday_delta = calculate_intraday_delta(ticker, window_minutes=30)
    delta_score = intraday_delta * 0.30

    # Volume scoring (two components: rate + absolute)
    volume_rate = calculate_volume_rate(ticker, window_minutes=30)
    rate_score = (volume_rate / 1000.0) * 0.15

    # Absolute volume bonus (rewards high volume even on first scan)
    # Scale: 1M vol = 1 point, 100M vol = 10 points, 500M vol = 15 points (logarithmic)
    import math
    volume_millions = current_volume / 1_000_000.0
    abs_vol_score = min(math.log10(max(volume_millions, 0.1)) * 5.0, 15.0) if volume_millions > 0 else 0

    vol_score = rate_score + abs_vol_score

    # Velocity bonus: did price change recently?
    has_pulse, _ = has_heartbeat(ticker, min_price_change_pct=0.5, min_volume_growth=1000)
    velocity_bonus = 20.0 if has_pulse else 0.0

    composite = gap_score + delta_score + vol_score + velocity_bonus

    return composite


# ---- Scheduler hook (Phase 1) -------------------------------------------------
def maybe_run_scheduler_dry_run() -> None:
    """If SCHEDULER_DRY_RUN env var is set, run the scheduler dry-run and write logs.

    This is intentionally non-invasive: it only runs when explicitly requested via
    environment variable and uses the Scheduler dry_run API which is read-only.
    """
    import os
    out = os.getenv("SCHEDULER_DRY_RUN")
    if not out:
        return

    # If Scheduler couldn't be imported, just warn and return
    if Scheduler is None:
        logging.getLogger("scanner").warning("Scheduler module not available; skipping dry-run")
        return

    try:
        cfg = load_config()
    except Exception:
        cfg = None

    sched = Scheduler(config=cfg)
    log_path = Path("logs") / "scheduler_dryrun.log"
    try:
        sched.dry_run(output_path=log_path, max_lines=200)
        logging.getLogger("scanner").info(f"Scheduler dry-run completed and written to {log_path}")
    except Exception:
        logging.getLogger("scanner").exception("Scheduler dry-run failed")




def _compute_display_score_from_row(row: dict) -> float:
    """Compute a lightweight display score for sorting/logging purposes.

    This mirrors the local scoring used as a fallback but does NOT call
    heartbeat (no velocity bonus) so it can be computed for all candidates.
    """
    try:
        w = {
            "gap": 0.4,
            "rvol": 0.3,
            "atr": 0.2,
        }
        gap = float(row.get("gap_pct") or 0.0)
        rvol = float(row.get("rvol") or 0.0)
        atr = float(row.get("atr_stretch") or 0.0)
        score = (w["gap"] * gap) + (w["rvol"] * min(rvol, 5.0)) + (w["atr"] * min(atr, 5.0))
        return float(score)
    except Exception:
        return 0.0


def _format_full_labels(row: dict, heartbeat_tag: str | None, display_score: float) -> str:
    """Return formatted log line with full labels: gap=.. last=.. open=.. vol=.. plus atr_stretch/rvol/score"""
    ticker = row.get("ticker") or "N/A"
    gap = row.get("gap_pct")
    last = row.get("last_price") or row.get("price") or ""
    open_ref = row.get("open_price") if row.get("open_price") is not None else row.get("prev")
    vol = row.get("volume") or row.get("intraday_volume") or 0

    gap_str = _fmt_gap(last, open_ref) if (last and open_ref) else (f"{gap:+.1f}%" if isinstance(gap, (int, float)) else str(gap or "n/a"))
    last_str = _fmt_num(last, 2)
    open_str = _fmt_num(open_ref, 2)
    vol_str = _fmt_vol(vol)

    atr = row.get("atr_stretch") or "-"
    rvol = row.get("rvol") or "-"

    hb = f" [{heartbeat_tag}]" if heartbeat_tag else ""

    return f"{ticker}: gap={gap_str} last={last_str} open={open_str} vol={vol_str}{hb} atr_stretch={atr} rvol={rvol} score={display_score:.1f}"

## -------------------------------------------------------------------
## BLOCK: deficient-ticker-filter  |  FILE: src/scanner/scanner.py  |  DATE: 2025-10-03
## PURPOSE: Fast filter for non-tradeable tickers (suffix-based only)
## NOTES:
##   - Deficient stocks filtered by $1.00 minimum price in liquidity config
##   - This only checks for rights, warrants, units, etc.
## -------------------------------------------------------------------
def is_ticker_tradeable_fast(ticker: str) -> tuple[bool, str]:
    """
    Fast check if ticker is tradeable (suffix-based filter only).

    Returns:
        (is_tradeable: bool, reason: str)
    """
    ticker_upper = ticker.upper()

    # Check specific suffix patterns (must be specific to avoid false positives like MSTR)
    # Rights: typically single letter + R (e.g., BAYAR = BAYA + R)
    if len(ticker_upper) >= 5 and ticker_upper[-1] == 'R' and ticker_upper[-2].isalpha():
        # Check if it's a known legitimate ticker ending in R
        legitimate_r_tickers = {'MSTR', 'TIGR', 'UBER', 'HEAR', 'FSLR', 'SOTR', 'CHTR'}
        if ticker_upper not in legitimate_r_tickers:
            # Could be a rights offering (e.g., BAYAR)
            # But only flag if ticker base + R pattern (conservative)
            pass  # Allow through for now - too many false positives

    # Multi-letter suffixes (more specific)
    if ticker_upper.endswith('WS'):  # Warrants
        return False, "suffix_WS"
    if ticker_upper.endswith('.W'):  # Warrants (dot notation)
        return False, "suffix_.W"

    # Single letter suffixes at end (after 4+ letter base)
    if len(ticker_upper) >= 5:
        last_char = ticker_upper[-1]
        # Only flag if last char is one of these AND it's clearly a derivative
        if last_char in ('W', 'U', 'Z', 'Q', 'E') and ticker_upper[-2].isalpha():
            return False, f"suffix_{last_char}"

    return True, "tradeable"

## -------------------------------------------------------------------
## BLOCK: dated-logger  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Write logs to logs/scanner_YYYY-MM-DD.log; prevent duplicate handlers
## NOTES:
##   - Uses cfg["output"]["log"] only to determine the directory.
##   - If cfg points to a file (e.g., logs/scanner.log), we keep that *directory*
##     and replace the filename with scanner_<today>.log.
## -------------------------------------------------------------------
def setup_logger(log_path: str) -> logging.Logger:
    # Resolve directory to place the dated log file
    log_path = Path(log_path)
    log_dir = log_path.parent if log_path.suffix else log_path
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build dated filename
    today_str = datetime.now().strftime("%Y-%m-%d")
    dated_file = log_dir / f"scanner_{today_str}.log"

    logger = logging.getLogger("scanner")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers on repeated invocations
    if not any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == str(dated_file)
        for h in logger.handlers
    ):
        fh = logging.FileHandler(dated_file, mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

    return logger


# -------------------------------------------------------------------
# Time helpers
# -------------------------------------------------------------------
## -------------------------------------------------------------------
## BLOCK: premarket-cap-0930  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Ensure premarket loop always stops at 09:30 ET, regardless of YAML end_time
## -------------------------------------------------------------------
## -------------------------------------------------------------------
## PATCH: premarket-window-respect-yaml
## FILE: src/scanner/scanner.py
## PURPOSE: Allow YAML end_time to extend beyond 09:30 if explicitly set.
## NOTES:
##   - Default behavior (no YAML override): still capped at 09:30.
##   - If YAML end_time > 09:30, we respect YAML (useful for testing / noon extension).
## -------------------------------------------------------------------
def within_premarket_window(cfg: dict) -> bool:
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)

    # Times from YAML
    start_cfg = datetime.strptime(cfg["premarket"]["start_time"], "%H:%M").time()
    end_cfg   = datetime.strptime(cfg["premarket"]["end_time"], "%H:%M").time()

    # Default cap at 09:30
    cap0930 = datetime.strptime("09:30", "%H:%M").time()

    # Respect YAML if explicitly set later than 09:30
    effective_end = end_cfg if end_cfg > cap0930 else cap0930

    start_dt = tz.localize(datetime.combine(now.date(), start_cfg))
    end_dt   = tz.localize(datetime.combine(now.date(), effective_end))

    return start_dt <= now <= end_dt


def is_final_pick_time() -> bool:
    tz = pytz.timezone("America/New_York")
    return datetime.now(tz).strftime("%H:%M") == "09:50"


## -------------------------------------------------------------------
## BLOCK: open-selection-window  |  FILE: src/scanner/scanner.py
## PURPOSE: True only during the 09:30–09:35 ET selection window (configurable)
## -------------------------------------------------------------------
def within_open_selection_window(cfg: dict) -> bool:
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)

    # Configurable window, default "09:30-09:35"
    win = (cfg.get("open_selection", {}) or {}).get("selection_window", "09:30-09:35")
    try:
        start_s, end_s = win.split("-")
        start_t = datetime.strptime(start_s.strip(), "%H:%M").time()
        end_t   = datetime.strptime(end_s.strip(), "%H:%M").time()
    except Exception:
        start_t = datetime.strptime("09:30", "%H:%M").time()
        end_t   = datetime.strptime("09:35", "%H:%M").time()

    start_dt = tz.localize(datetime.combine(now.date(), start_t))
    end_dt   = tz.localize(datetime.combine(now.date(), end_t))
    return start_dt <= now <= end_dt


## -------------------------------------------------------------------
## BLOCK: prefilter-top-gappers-v2  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Rank by %Gap using robust snapshot fields:
##   - Prefer last_price; fallback to close (regular session close)
##   - Require prev_close > 0; enforce min_price on chosen price
##   - Adds basic diagnostics to help explain empty pools
## -------------------------------------------------------------------
def prefilter_top_gappers(snaps: list[dict], n: int = 100, min_price: float = 1.0) -> list[dict]:
    pool = []
    skipped_missing = 0
    skipped_price   = 0
    skipped_suffix  = 0
    for s in snaps or []:
        ticker = s.get("ticker", "")
        # Filter out non-tradeable: OTC (F), warrants (W), ADRs (Y), units (U), preferreds (PS/AS/WS/RS)
        excluded_endings = ("F", "W", "Y", "U", "PS", "AS", "WS", "RS")
        if ticker and any(ticker.endswith(suffix) for suffix in excluded_endings):
            skipped_suffix += 1
            continue

        # choose a price: last_price (preferred), else close
        last = s.get("last_price")
        close = s.get("close")
        price = last if last is not None else close

        prev = s.get("prev_close")
        if price is None or prev is None or prev <= 0:
            skipped_missing += 1
            continue
        try:
            price_f = float(price)
            prev_f  = float(prev)
        except Exception:
            skipped_missing += 1
            continue

        if price_f < float(min_price):
            skipped_price += 1
            continue

        try:
            gap = (price_f - prev_f) / prev_f * 100.0
        except Exception:
            skipped_missing += 1
            continue

        # EARLY SNAPSHOT: record a lightweight snapshot now for heartbeat history
        # if the ticker meets the lightweight prefilter. This prevents later
        # downstream filters from causing the ticker to appear as a "first"
        # observation during the active session.
        try:
            record_snapshot(ticker, price_f, int(s.get("volume", 0) or 0))
        except Exception:
            pass

        s2 = dict(s)
        s2["_gap_pct_snapshot"] = gap
        s2["_prefilter_price"]  = price_f  # for optional debugging
        pool.append(s2)

    pool.sort(key=lambda x: x.get("_gap_pct_snapshot", 0.0), reverse=True)

    # Optional: quick diagnostic line if pool ends up empty (uses global logger if present)
    try:
        logger = logging.getLogger("scanner")
        if not pool:
            logger.info(f"ℹ️ [Open] Prefilter diagnostics: skipped_missing={skipped_missing}, skipped_price={skipped_price}, skipped_suffix={skipped_suffix}")
    except Exception:
        pass

    return pool[:n]


## -------------------------------------------------------------------
## BLOCK: group-watchlist-log  |  FILE: src/scanner/scanner.py
## PURPOSE: Log a configurable group watchlist using snapshot fields only
## NOTES: Logs only (no CSV, no enrichment). Fast: pure in-memory lookup.
## -------------------------------------------------------------------
def _fmt_num(x, d=2):
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "?"

def _fmt_gap(p, prev):
    try:
        p, prev = float(p), float(prev)
        if prev <= 0:
            return "n/a"
        return f"{((p - prev) / prev) * 100.0:+.1f}%"
    except Exception:
        return "n/a"

def _fmt_vol(v):
    try:
        v = int(v or 0)
        if v >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v/1_000:.1f}K"
        return str(v)
    except Exception:
        return "0"

def log_group_watchlist(cfg: dict, snaps: list[dict], logger: logging.Logger) -> None:
    # Load from CSV first, fallback to YAML if CSV doesn't exist
    symbols = load_group_watchlist()
    if not symbols:
        symbols = (cfg.get("group_watchlist") or [])
    if not symbols:
        return

    # build quick index by ticker
    by_sym = {}
    for s in snaps or []:
        sym = s.get("ticker")
        if sym:
            by_sym[sym] = s

    logger.info("📋 Group Watchlist:")
    for sym in symbols:
        s = by_sym.get(sym)
        if not s:
            logger.info(f"   {sym}: (no snapshot)")
            continue

        last = s.get("last_price")
        close = s.get("close")
        prev  = s.get("prev_close")
        price = last if last not in (None, 0) else close

        # Record market open price on first scan after 9:30
        if price and is_market_open():
            try:
                record_market_open_price(sym, float(price))
            except:
                pass

        # Calculate gap: use market open price if available (after 9:30), else prev_close
        open_price = get_market_open_price(sym)
        if open_price is not None:
            # Intraday change from market open
            ref_price = open_price
            gap_label = "chg"  # "change from open" instead of "gap"
        else:
            # Premarket gap from yesterday's close
            ref_price = prev
            gap_label = "gap"

        gap_str = _fmt_gap(price, ref_price) if (price and ref_price) else "n/a"
        last_str = _fmt_num(price, 2)
        ref_str = _fmt_num(ref_price, 2)
        vol_str  = _fmt_vol(s.get("volume"))

        # Check if ticker is deficient and add [D] tag
        is_tradeable, reason = is_ticker_tradeable_fast(sym)
        deficient_tag = "" if is_tradeable else " [D]"

        logger.info(f"   {sym}{deficient_tag}: {gap_label}={gap_str} last={last_str} open={ref_str} vol={vol_str}")

# -------------------------------------------------------------------
# CSV bootstrap / append
# -------------------------------------------------------------------
CSV_FIELDS = [
    "date",
    "time",
    "ticker",
    "gap_pct",
    "rvol",
    "atr_stretch",
    "premarket_high",
    "open_price",
    "score",
    "final_pick",
    "rationale",
]


def ensure_today_pick_ready(cfg: dict, reset: bool = True) -> Path:
    """Reset output/today_pick.csv from schemas/today_pick_template.csv (keeps headers correct)."""
    out_path = Path(cfg["output"].get("today_pick", "output/today_pick.csv"))
    tpl_path = ROOT / "schemas" / "today_pick_template.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if reset or not out_path.exists():
        shutil.copyfile(tpl_path, out_path)
    return out_path


def append_row(row: dict, cfg: dict) -> None:
    csv_path = Path(cfg["output"].get("today_pick", "output/today_pick.csv"))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        # write row with only expected fields (order guaranteed by CSV_FIELDS)
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


# -------------------------------------------------------------------
# Enrichment (adds prev_close, ATR, gap %, RVOL, ATR_Stretch)
# -------------------------------------------------------------------
from datetime import datetime
import pytz


def enrich_rows(
    snapshots: list[dict], trade_date: str, cfg: dict | None = None
) -> list[dict]:
    """
    snapshots: list of dicts from fetch_snapshots(); must include "ticker".
    Returns rows enriched for scoring & CSV output.
    """
    enriched: list[dict] = []
    for s in snapshots:
        tkr = s.get("ticker")
        if not tkr:
            continue

        prev_close = pa.get_previous_close(tkr)
        pm_high = pa.get_premarket_high(tkr, trade_date)
        atr_14 = pa.get_atr_14(tkr, trade_date)

        # Pull intraday/avg volumes from adapter
        intraday_vol = pa.get_intraday_volume(tkr, trade_date)
        avg_vol = pa.get_avg_daily_volume(tkr, lookback=20)

        # Prices: prefer snapshot last_price if present; fall back gracefully
        last_price = s.get("last_price") or prev_close or 0.0
        open_price = last_price  # TODO: replace with pa.get_open_price_0930 later

        gap_pct = (
            ((open_price - prev_close) / prev_close * 100.0) if prev_close else None
        )
        rvol = (intraday_vol / avg_vol) if avg_vol else None
        atr_stretch = ((last_price - prev_close) / atr_14) if atr_14 else None

        enriched.append(
            {
                "date": trade_date,
                "time": datetime.now().strftime("%H:%M"),
                "ticker": tkr,
                "premarket_high": pm_high,
                "open_price": open_price,
                "last_price": last_price,
                "intraday_volume": intraday_vol,
                "avg_daily_volume": avg_vol,
                "prev_close": prev_close,
                "gap_pct": round(gap_pct, 2) if gap_pct is not None else "",
                "rvol": round(rvol, 2) if rvol is not None else "",
                "atr_14": atr_14 if atr_14 else "",
                "atr_stretch": round(atr_stretch, 2) if atr_stretch is not None else "",
            }
        )
    return enriched


# -------------------------------------------------------------------
# Liquidity filters (session-aware)
# -------------------------------------------------------------------
def _dollar_volume(row: dict) -> float:
    try:
        return float(row.get("last_price", 0)) * int(row.get("intraday_volume", 0))
    except Exception:
        return 0.0


## -------------------------------------------------------------------
## BLOCK: liq-select-with-session  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Return both thresholds and the session label ("premarket"/"regular")
## -------------------------------------------------------------------
def get_liquidity_cfg(cfg: dict) -> tuple[dict, str]:
    ny = pytz.timezone("America/New_York")
    now_et = datetime.now(ny)
    session = (
        "premarket"
        if (now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30))
        else "regular"
    )

    liq_root = cfg.get("liquidity", {})
    if session in liq_root:
        return liq_root[session], session
    # Fallback if dual blocks not present
    return liq_root, session

## -------------------------------------------------------------------
## PATCH: liq-apply-session-log (FIX TUPLE UNPACK) 
## FILE: src/scanner/scanner.py
## PURPOSE: Unpack (liq_cfg, session) and log correctly
## -------------------------------------------------------------------
def apply_liquidity_filters(
    rows: list[dict], cfg: dict, logger: logging.Logger | None = None
) -> list[dict]:
    liq_cfg, session = get_liquidity_cfg(cfg)
    out: list[dict] = []
    dropped = {"missing_prices": 0, "price_floor": 0, "shares": 0, "avg": 0, "dollar": 0}

    for r in rows:
        last = r.get("last_price")
        prev = r.get("prev_close")

        if liq_cfg.get("require_prices", True) and (not last or not prev or prev == 0):
            dropped["missing_prices"] += 1
            continue
        if float(last or 0.0) < float(liq_cfg.get("min_price", 0.0)):
            dropped["price_floor"] += 1
            continue
        if int(r.get("intraday_volume", 0) or 0) < int(liq_cfg.get("min_intraday_shares", 0)):
            dropped["shares"] += 1
            continue
        if int(r.get("avg_daily_volume", 0) or 0) < int(liq_cfg.get("min_avg_daily_volume", 0)):
            dropped["avg"] += 1
            continue
        if _dollar_volume(r) < float(liq_cfg.get("min_dollar_volume", 0.0)):
            dropped["dollar"] += 1
            continue

        out.append(r)

    if logger:
        logger.info(f"💧 Liquidity gate ({session}): {len(out)}/{len(rows)} passed (dropped {dropped})")
    return out

# -------------------------------------------------------------------
# Main loop (premarket) and one-shot entrypoint
# -------------------------------------------------------------------
def _final_pick_row(base: dict, score: float, final: bool, rationale: str) -> dict:
    """Map a scored/enriched item to the CSV schema row."""
    return {
        "date": base.get("date", ""),
        "time": base.get("time", datetime.now().strftime("%H:%M")),
        "ticker": base.get("ticker", ""),
        "gap_pct": base.get("gap_pct", ""),
        "rvol": base.get("rvol", ""),
        "atr_stretch": base.get("atr_stretch", ""),
        "premarket_high": base.get("premarket_high", ""),
        "open_price": base.get("open_price", ""),
        "score": round(score, 3) if isinstance(score, (int, float)) else score,
        "final_pick": "TRUE" if final else "FALSE",
        "rationale": rationale or "",
    }


def _pick_winner(scored: list[dict]) -> dict | None:
    """Select winner as top-scored row (assumes score_snapshots returned 'score' on each)."""
    if not scored:
        return None
    return max(scored, key=lambda r: r.get("score", float("-inf")))

## -------------------------------------------------------------------
## BLOCK: scoring-adapter  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Call core.scoring.score_snapshots safely and normalize output.
## RETURNS: (scored_rows_sorted, winner_row)
## -------------------------------------------------------------------
def score_and_pick(filtered: list[dict], cfg: dict, logger: logging.Logger) -> tuple[list[dict], dict | None]:
    def _local_score(row: dict) -> float:
        w = cfg.get("scoring_weights", {})
        w_gap = float(w.get("gap_percent", 0.4))
        w_rvol = float(w.get("rvol", 0.3))
        w_atr  = float(w.get("atr_stretch", 0.2))
        gap = float(row.get("gap_pct") or 0.0)
        rvol = float(row.get("rvol") or 0.0)
        atrs = float(row.get("atr_stretch") or 0.0)
        return (w_gap * gap) + (w_rvol * min(rvol, 5.0)) + (w_atr * min(atrs, 5.0))

    # Build the dict shape some score_snapshots implementations expect
    snap_dict = {r.get("ticker"): r for r in filtered if r.get("ticker")}
    if not snap_dict:
        return [], None

    scored_rows: list[dict] = []
    try:
        t0 = time.time()
        try:
            scored_out = score_snapshots(snap_dict)  # prefer single-arg API
        except TypeError:
            scored_out = score_snapshots(snap_dict, cfg.get("scoring_weights", {}))
        dt = time.time() - t0
        logger.info(f"🏎️ Scoring completed in {dt:.2f}s for {len(filtered)} candidates")

        # Normalize to list[dict] with 'score'
        if isinstance(scored_out, dict):
            for tkr, val in scored_out.items():
                base = snap_dict.get(tkr, {}).copy()
                if isinstance(val, dict):
                    base.update(val)
                    if "score" not in base and "score" in val:
                        base["score"] = val["score"]
                else:
                    base["score"] = float(val)
                scored_rows.append(base)

        elif isinstance(scored_out, list):
            for item in scored_out:
                if isinstance(item, dict):
                    tkr = item.get("ticker")
                    base = snap_dict.get(tkr, {}).copy() if tkr else {}
                    base.update(item)
                    if "score" not in base:
                        base["score"] = _local_score(base)
                    scored_rows.append(base)
                elif isinstance(item, (tuple, list)) and len(item) >= 2:
                    tkr, score_val = item[0], item[1]
                    base = snap_dict.get(tkr, {}).copy()
                    base["score"] = float(score_val) if score_val is not None else _local_score(base)
                    scored_rows.append(base)
        else:
            # Unknown return type: compute local scores
            for tkr, base in snap_dict.items():
                row = base.copy()
                row["score"] = _local_score(row)
                scored_rows.append(row)

    except Exception as e:
        logger.error(f"❌ Scoring adapter failed, using local scores: {e}", exc_info=True)
        for tkr, base in snap_dict.items():
            row = base.copy()
            row["score"] = _local_score(row)
            scored_rows.append(row)

    if not scored_rows:
        return [], None

    scored_rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    winner = scored_rows[0]
    return scored_rows, winner

## -------------------------------------------------------------------
## BLOCK: run-once-patched  |  FILE: src/scanner/scanner.py  |  DATE: 2025-09-29
## PURPOSE: Ensure apply_liquidity_filters sees full cfg (not cfg["liquidity"])
## NOTES:
##   - Fixes bug where all rows were dropped silently
##   - Now get_liquidity_cfg(cfg) can choose premarket vs regular
## -------------------------------------------------------------------
def run_once(cfg: dict, logger: logging.Logger, force_final_pick: bool = False) -> None:
    """Single pass (used by --once and by the loop)."""
    today = datetime.now().strftime("%Y-%m-%d")
    ensure_today_pick_ready(cfg, reset=False)  # keep file; created earlier

    # --- Debug mode: always inject dummy row, skip rest ---
    if cfg.get("debug", {}).get("enable", False):
        dbg = cfg["debug"]
        dummy = {
            "date": today,
            "time": datetime.now().strftime("%H:%M"),
            "ticker": dbg["dummy_ticker"],
            "gap_pct": dbg["dummy_gap"],
            "rvol": 1.0,
            "atr_stretch": 1.0,
            "premarket_high": 0,
            "open_price": dbg["dummy_price"],
        }
        row = _final_pick_row(
            dummy,
            score=dbg["dummy_score"],
            final=True,
            rationale=dbg.get("rationale", "Debug"),
        )
        append_row(row, cfg)
        logger.info(
            f"⚠️ Debug: Appended dummy Final Pick -> {cfg['output'].get('today_pick', 'output/today_pick.csv')}"
        )
        return

    # --- Normal path ---
    snaps = fetch_snapshots(limit=50000)
    logger.info(f"📡 Snapshots fetched: {len(snaps)} tickers")

    # CALL: group-watchlist (logs only)
    try:
        log_group_watchlist(cfg, snaps, logger)
    except Exception as e:
        logger.warning(f"[GROUP] failed to log watchlist: {e}")


    # --- NEW: Premarket/Postmarket short-circuit ---
    ny = pytz.timezone("America/New_York")
    now_et = datetime.now(ny)
    is_premarket = now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30)
    is_postmarket = now_et.hour >= 16 and now_et.hour < 20

    if is_premarket or is_postmarket:
        # Just compute raw %Gap from bulk feed
        def _best_price_from_snapshot(s: dict):
            # Order matters: last_price (extended ok) → day.c → day.o → day.h → day.l
            # We already mapped these into snapshot keys: last_price, close, open, high, low
            for k in ("last_price", "close", "open", "high", "low"):
                v = s.get(k)
                try:
                    v = float(v)
                    if v > 0:
                        return v
                except Exception:
                    pass
            return None

        top = []
        for s in snaps or []:
            ticker = s.get("ticker", "")
            # Filter out non-tradeable: OTC (F), warrants (W), ADRs (Y), units (U), preferreds (PS/AS/WS/RS)
            excluded_endings = ("F", "W", "Y", "U", "PS", "AS", "WS", "RS")
            if ticker and any(ticker.endswith(suffix) for suffix in excluded_endings):
                continue

            # Filter: require minimum volume to confirm actual premarket trading
            vol = s.get("volume", 0)
            try:
                vol = int(vol) if vol else 0
            except Exception:
                vol = 0
            # Require at least 10K shares traded (filters stale/no-trading tickers)
            if vol < 10000:
                continue

            prev = s.get("prev_close")
            price = _best_price_from_snapshot(s)
            try:
                prev = float(prev) if prev is not None else None
            except Exception:
                prev = None
            # Filter: require both price >= $1.00 AND prev_close >= $1.00 (NASDAQ compliance)
            if price is None or prev is None or price < 1.0 or prev < 1.0:
                continue
            try:
                gap = (price - prev) / prev * 100.0
                # EARLY SNAPSHOT: record now so later filters don't prevent
                # heartbeat from seeing this ticker as an observed history item.
                try:
                    record_snapshot(ticker, price, vol)
                except Exception:
                    pass
                top.append((ticker, gap, price, prev, vol))
            except Exception:
                continue

        top.sort(key=lambda x: x[1], reverse=True)
        top5 = top[:5]

        session_label = "Premarket" if is_premarket else "Postmarket"

        # Composite scoring with heartbeat filter (filters out stale tickers)
        logger.info(f"🏁 [{session_label}] Top 5 by Composite Score:")
        scored_candidates = []
        for ticker, gap_pct, price, prev, vol in top:
            # Check 1: Tradeability (suffix-based filter + NASDAQ deficiency rule)
            is_tradeable, trade_reason = is_ticker_tradeable_fast(ticker)
            if not is_tradeable:
                continue  # Skip non-tradeable tickers (silent filter)

            # Check 2: Heartbeat (is it moving?)
            has_pulse, reason = has_heartbeat(ticker, min_price_change_pct=0.5, min_volume_growth=1000)
            if not has_pulse:
                logger.debug(f"   ❌ {ticker} FILTERED: {reason}")
                continue  # Skip stale tickers

            # Calculate composite score
            score = calculate_composite_score(ticker, gap_pct, price, vol)
            scored_candidates.append({
                'ticker': ticker,
                'score': score,
                'gap_pct': gap_pct,
                'price': price,
                'prev': prev,
                'volume': vol,
                'heartbeat': reason
            })

        scored_candidates.sort(key=lambda x: x['score'], reverse=True)

        if scored_candidates:
            # Always show Top-5 by display score (highest->lowest). Annotate heartbeat reason.
            for item in sorted(scored_candidates, key=lambda x: _compute_display_score_from_row(x), reverse=True)[:5]:
                display_score = _compute_display_score_from_row(item)
                hb = item.get('heartbeat')
                logger.info("   " + _format_full_labels(item, hb, display_score))
        else:
            logger.info(f"   (No active tickers with heartbeat - all are stale/frozen)")

        return  # 👈 exit early, normal case

        # -------------------------------------------------------------------
        # PATCH: premarket-fallback | FILE: src/scanner/scanner.py
        # DATE: 2025-09-30
        # PURPOSE: Guarantee "always shows something" premarket
        # -------------------------------------------------------------------
        fallback = []
        for s in snaps or []:
            ticker = s.get("ticker", "")
            # Filter out non-tradeable: OTC (F), warrants (W), ADRs (Y), units (U), preferreds (PS/AS/WS/RS)
            excluded_endings = ("F", "W", "Y", "U", "PS", "AS", "WS", "RS")
            if ticker and any(ticker.endswith(suffix) for suffix in excluded_endings):
                continue

            # Filter: require minimum volume to confirm actual premarket trading
            vol = s.get("volume", 0)
            try:
                vol = int(vol) if vol else 0
            except Exception:
                vol = 0
            # Require at least 10K shares traded (filters stale/no-trading tickers)
            if vol < 10000:
                continue

            # pick best available price
            price = None
            for k in ("last_price", "close", "open", "high", "low"):
                v = s.get(k)
                try:
                    v = float(v)
                    if v > 0:
                        price = v
                        break
                except Exception:
                    continue

            prev = s.get("prev_close")
            try:
                prev = float(prev) if prev is not None else None
            except Exception:
                prev = None

            # Filter: require prev >= $0.10 to avoid garbage data (but allow penny stocks)
            if price is None or prev is None or prev < 0.10:
                continue
            try:
                gap = (price - prev) / prev * 100.0
                # EARLY SNAPSHOT (fallback path): ensure heartbeat history exists
                # even for fallback candidates so the active loop can use it.
                try:
                    record_snapshot(ticker, price, vol)
                except Exception:
                    pass
                fallback.append((ticker, gap, price, prev, vol))
            except Exception:
                continue

        fallback.sort(key=lambda x: x[1], reverse=True)
        top5_fallback = fallback[:5]
        if top5_fallback:
            logger.info(f"🏁 [Fallback-{session_label}] Top 5 by %Gap:")
            for t, g, price, prev, vol in top5_fallback:
                gap_str = _fmt_gap(price, prev)
                last_str = _fmt_num(price, 2)
                prev_str = _fmt_num(prev, 2)
                vol_str = _fmt_vol(vol)
                logger.info(f"   {t}: gap={gap_str} last={last_str} prev={prev_str} vol={vol_str}")
        else:
            logger.info(f"🏁 [Fallback-{session_label}] Top 5 by %Gap: (still empty)")
        return  # 👈 always exit after logging

## -------------------------------------------------------------------
## BLOCK: open-selection-pass  |  FILE: src/scanner/scanner.py
## PURPOSE: One pass after 09:30 — enrich candidates, filter, score, write Pick of the Day
## -------------------------------------------------------------------
def run_open_selection_once(cfg: dict, logger: logging.Logger, force: bool = False) -> None:
    # Only act inside the configured window (unless forced by fallback)
    if not force and not within_open_selection_window(cfg):
        logger.info("Open selection window not active — skipping open selection pass.")
        return

    # Fetch full snapshots (cheap)
    snaps = fetch_snapshots(limit=50000)
    logger.info(f"📡 [Open] Snapshots fetched: {len(snaps)} tickers")

    # CALL: group-watchlist during open window (logs only)
    try:
        log_group_watchlist(cfg, snaps, logger)
    except Exception as e:
        logger.warning(f"[GROUP][Open] failed to log watchlist: {e}")


    # Candidate pool (Top-N by %Gap from snapshots)
    pool_n = int((cfg.get("open_selection", {}) or {}).get("candidate_pool_size", 100))
    # Use min_price=0.25 to match liquidity.regular.min_price config
    candidates = prefilter_top_gappers(snaps, n=pool_n, min_price=0.25)
    logger.info(f"⚡ [Open] Candidate pool from snapshots (Top {pool_n} by %Gap): {len(candidates)}")

    if not candidates:
        logger.warning("[Open] No candidates available for selection.")
        return

    # Enrich only the candidates
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        enriched = enrich_rows(candidates, today)
        logger.info(f"🧪 [Open] Enrichment produced {len(enriched)} rows")
    except Exception as e:
        logger.error(f"❌ [Open] Enrichment failed: {e}", exc_info=True)
        return

    # Regular-session liquidity (pass full cfg so session logic selects 'regular')
    try:
        filtered = apply_liquidity_filters(enriched, cfg, logger)
        logger.info(f"💧 [Open] Liquidity result: {len(filtered)}/{len(enriched)} passed")
    except Exception as e:
        logger.error(f"❌ [Open] Liquidity filter failed: {e}", exc_info=True)
        return

    if not filtered:
        logger.warning("[Open] No survivors after liquidity — no Pick of the Day.")
        return

    # -------------------------------------------------------------------
    # BLOCK: open-scoring-with-fallback  |  DATE: 2025-10-01
    # PURPOSE: Robust scoring + CSV write with timing and fallback pick
    # FIX: Convert list to dict before passing to score_snapshots
    # -------------------------------------------------------------------
    try:
        t0 = time.time()
        # Convert filtered list to dict {ticker: data} for scoring
        filtered_dict = {r.get('ticker'): r for r in filtered if r.get('ticker')}
        scored = score_snapshots(filtered_dict)
        dt = time.time() - t0
        logger.info(f"🏎️ [Open] Scoring completed in {dt:.2f}s for {len(filtered)} candidates")

        # Log top-5 (by score) for visibility
        log_top_movers(scored, n=5)

        # Log top 3 candidates for manual review (in case #1 is not tradeable)
        logger.info("🎯 [Open] Top 3 Candidates for Pick of the Day:")
        for i, candidate in enumerate(scored[:3], 1):
            ticker = candidate.get('ticker', 'N/A')
            score = candidate.get('score', 0)
            gap = candidate.get('gap_pct', 0)
            rvol = candidate.get('rvol', 0)
            logger.info(f"  #{i}: {ticker} - score={score:.1f}, gap={gap:.1f}%, rvol={rvol:.1f}x")
        # PROTECTION: Run deep deficiency checks only for top-N candidates to avoid
        # making many blocking API calls. Use polygon_adapter.is_ticker_deficient_fast
        # with a short timeout. If a candidate is deficient we skip it.
        try:
            from src.adapters.polygon_adapter import is_ticker_deficient_fast
            deep_limit = int((cfg.get("open_selection", {}) or {}).get("deep_deficiency_limit", 20))
            vetted: list[dict] = []
            for candidate in scored[: deep_limit]:
                tkr = candidate.get("ticker")
                try:
                    deficient, reason = is_ticker_deficient_fast(tkr, timeout=5)
                    if deficient:
                        logger.info(f"   [D] Skipping deficient ticker {tkr}: {reason}")
                        continue
                except Exception:
                    # On error, assume OK
                    pass
                vetted.append(candidate)
            # Append remaining scored items (beyond deep_limit) without deep checks
            if len(scored) > deep_limit:
                vetted.extend(scored[deep_limit:])
            winner = _pick_winner(vetted)
        except Exception:
            # If anything fails, fall back to naive picker
            winner = _pick_winner(scored)
        if not winner:
            raise RuntimeError("No winner returned by scoring")

    except Exception as e:
        logger.error(f"❌ [Open] Scoring failed, using fallback: {e}", exc_info=True)
        # Fallback selection: prefer highest RVOL, then highest $ volume, then highest %gap
        def _safe_float(x):
            try: return float(x)
            except Exception: return 0.0
        def _safe_int(x):
            try: return int(x)
            except Exception: return 0

        def _dollar_vol(r):
            return _safe_float(r.get("last_price")) * _safe_int(r.get("intraday_volume"))

        def _gap_pct(r):
            g = r.get("gap_pct")
            return _safe_float(g) if g not in (None, "") else 0.0

        try:
            winner = max(
                filtered,
                key=lambda r: (
                    _safe_float(r.get("rvol")),      # 1) RVOL
                    _dollar_vol(r),                  # 2) Dollar volume
                    _gap_pct(r)                      # 3) % gap
                ),
            )
            logger.info("[Open] Fallback selected winner by RVOL → $vol → %gap.")
        except Exception as ee:
            logger.error(f"❌ [Open] Fallback selection failed: {ee}", exc_info=True)
            return

    # Persist Pick of the Day (either scored or fallback)
    try:
        row = _final_pick_row(
            winner,
            score=winner.get("score", ""),  # may be empty under fallback
            final=True,
            rationale="Pick of the Day at open",
        )
        append_row(row, cfg)
        logger.info(
            f"🏅 [Open] Pick of the Day: {row['ticker']} (score={row['score']}) → "
            f"{cfg['output'].get('today_pick', 'output/today_pick.csv')}"
        )
    except Exception as e:
        logger.error(f"❌ [Open] Write to CSV failed: {e}", exc_info=True)
        return

def run() -> None:
    cfg = load_config()
    logger = setup_logger(cfg["output"]["log"])

    # Diagnostic: log process id and initial MARKET_OPEN_FIRST_SCAN state
    try:
        logger.info(f"PID={os.getpid()} MARKET_OPEN_FIRST_SCAN={MARKET_OPEN_FIRST_SCAN}")
    except Exception:
        pass

    # Always reset/seed today_pick with the template at process start
    ensure_today_pick_ready(cfg, reset=True)

    # Clear snapshot history from previous run (new trading day)
    clear_snapshot_history()
    logger.info("📝 Snapshot history cleared for new trading day")

    logger.info("Starting premarket scanner loop...")

    while within_premarket_window(cfg):
        try:
            run_once(cfg, logger, force_final_pick=False)
        except Exception as e:
            logger.error(f"❌ Error during scan tick: {e}", exc_info=True)

        # Dynamic cadence: 30min → 15min → 5min as we approach open
        cadence = get_scan_cadence_minutes()
        logger.info(f"⏱️ Next scan in {cadence} minutes")
        time.sleep(cadence * 60)

    logger.info("Premarket window closed.")

    # Wait until 9:15 AM to start active monitoring
    ny = pytz.timezone("America/New_York")
    while datetime.now(ny).hour < 9 or (datetime.now(ny).hour == 9 and datetime.now(ny).minute < 15):
        logger.info("⏳ Waiting for 9:15 AM to start active monitoring...")
        time.sleep(60)  # Check every minute

    logger.info("🔍 9:15 AM - Starting active watchlist monitoring (5-min cadence)...")
    pick_made = False

    while True:
        now = datetime.now(pytz.timezone("America/New_York"))

        # EXPLICIT CHECK: Trigger at exact selection window start time
        if not pick_made:
            sel_window = (cfg.get("open_selection", {}) or {}).get("selection_window", "09:50-09:55")
            try:
                start_s, end_s = sel_window.split("-")
                start_t = datetime.strptime(start_s.strip(), "%H:%M").time()
                end_t = datetime.strptime(end_s.strip(), "%H:%M").time()
            except Exception:
                start_t = datetime.strptime("09:50", "%H:%M").time()
                end_t = datetime.strptime("09:55", "%H:%M").time()

            # PRIORITY 1: Check if we're at the exact start time (09:50 sharp)
            if now.hour == start_t.hour and now.minute == start_t.minute:
                logger.info(f"⏰ {start_t.strftime('%H:%M')} ET - Selection window START - Making pick NOW...")
                try:
                    run_open_selection_once(cfg, logger, force=True)
                    pick_made = True
                    logger.info("📊 Pick completed. Continuing to monitor through market close...")
                except Exception as e:
                    logger.error(f"❌ Failed to make pick at window start: {e}", exc_info=True)
                    pick_made = True  # Prevent infinite retry

            # PRIORITY 2: Check if we're within the selection window (normal path)
            elif within_open_selection_window(cfg):
                logger.info("✅ Selection window active, making pick...")
                run_open_selection_once(cfg, logger)
                pick_made = True
                logger.info("📊 Continuing to monitor through market close...")

            # PRIORITY 3: Fallback if we missed the window entirely
            else:
                end_dt = pytz.timezone("America/New_York").localize(datetime.combine(now.date(), end_t))
                if now > end_dt:
                    logger.warning(f"⚠️ Missed selection window (ended {end_t.strftime('%H:%M')} ET). Forcing immediate pick at {now.strftime('%H:%M')} ET.")
                    try:
                        run_open_selection_once(cfg, logger, force=True)
                        pick_made = True
                        logger.info("📊 Late pick completed. Continuing to monitor through market close...")
                    except Exception as e:
                        logger.error(f"❌ Failed to make fallback pick: {e}", exc_info=True)
                        pick_made = True  # Prevent infinite retry

        # Stop at market close (4:00 PM ET)
        if now.hour >= 16:
            logger.info("🔔 Market closed (4:00 PM ET)")
            break

        # Log watchlist + top 5 every 5 minutes
        try:
            snaps = fetch_snapshots(limit=50000)
            logger.info(f"📡 Snapshot: {len(snaps)} tickers")

            # Log watchlist
            log_group_watchlist(cfg, snaps, logger)

            # Record snapshots to history & calculate composite scores
            logger.info("🏁 Top 5 (Composite Score - Heartbeat Active):")
            scored_tickers = []

            # Per-scan allowance: permit exactly one ticker in this active
            # scan to receive a 'first_market_pass' if history is insufficient.
            # This avoids multiple tickers getting the tag in the same cycle.
            allow_one_first_market_pass = False
            try:
                if MARKET_OPEN_FIRST_SCAN and is_market_open():
                    allow_one_first_market_pass = True
                    # Clear the global immediately so other processes/threads
                    # do not also attempt to use it.
                    try:
                        globals()["MARKET_OPEN_FIRST_SCAN"] = False
                    except Exception:
                        pass
                    logger.info("🔔 First market scan allowance set for this cycle")
            except Exception:
                allow_one_first_market_pass = False

            for s in snaps:
                ticker = s.get("ticker", "")
                excluded_endings = ("F", "W", "Y", "U", "PS", "AS", "WS", "RS")
                if ticker and any(ticker.endswith(suffix) for suffix in excluded_endings):
                    continue

                vol = s.get("volume", 0)
                try:
                    vol = int(vol) if vol else 0
                except Exception:
                    vol = 0
                if vol < 10000:
                    continue

                prev = s.get("prev_close")
                price = s.get("last_price")
                # Filter: require both price >= $1.00 AND prev_close >= $1.00 (NASDAQ compliance)
                if price is None or prev is None:
                    continue
                try:
                    price = float(price)
                    prev = float(prev)
                    if price < 1.0 or prev < 1.0:
                        continue
                except:
                    continue

                try:
                    price_f = float(price)
                    prev_f = float(prev)

                    # Record market open price on first scan after 9:30
                    if is_market_open():
                        record_market_open_price(ticker, price_f)

                    # Calculate gap: use market open price if available, else prev_close
                    open_price = get_market_open_price(ticker)
                    if open_price is not None:
                        # Intraday % change from market open
                        gap_pct = (price_f - open_price) / open_price * 100.0
                    else:
                        # Premarket gap from prev_close
                        gap_pct = (price_f - prev_f) / prev_f * 100.0

                    # Record snapshot to history
                    record_snapshot(ticker, price_f, vol)

                    # Check 1: Tradeability (suffix-based filter only - fast path)
                    is_tradeable, trade_reason = is_ticker_tradeable_fast(ticker)
                    if not is_tradeable:
                        continue  # Skip non-tradeable tickers (silent filter)

                    # Check 2: Heartbeat (is it moving?)
                    # If we allowed one first-market-pass for this scan, pass a
                    # flag to has_heartbeat so it can issue the one-time tag
                    # only for a single candidate.
                    has_pulse = False
                    reason = ""
                    try:
                        if allow_one_first_market_pass:
                            # Try with allowance; has_heartbeat will consume the
                            # allowance via the global USED flag we set earlier.
                            has_pulse, reason = has_heartbeat(ticker, min_price_change_pct=0.5, min_volume_growth=1000)
                            # If this ticker received the first_market_pass, mark
                            # that we've consumed the per-scan allowance so no
                            # other ticker can be granted it during this scan.
                            if reason == "first_market_pass":
                                allow_one_first_market_pass = False
                        else:
                            has_pulse, reason = has_heartbeat(ticker, min_price_change_pct=0.5, min_volume_growth=1000)
                    except Exception:
                        has_pulse, reason = False, "heartbeat_error"
                    if not has_pulse:
                        continue  # Skip stagnant tickers

                    # Calculate composite score
                    score = calculate_composite_score(ticker, gap_pct, price_f, vol)

                    scored_tickers.append({
                        'ticker': ticker,
                        'score': score,
                        'gap_pct': gap_pct,
                        'price': price_f,
                        'prev': prev_f,
                        'open_price': open_price if open_price is not None else prev_f,  # Store reference price for logging
                        'volume': vol,
                        'heartbeat': reason
                    })
                except Exception:
                    continue

            # Sort by composite score
            scored_tickers.sort(key=lambda x: x['score'], reverse=True)

            # After scoring, clear the MARKET_OPEN_FIRST_SCAN flag so subsequent
            # market scans will use full heartbeat behavior.
            try:
                # Diagnostic: log the flag and market state so it appears in
                # the normal INFO-level log output. This helps debug why the
                # first-market-pass tag may persist across scans.
                logger.info(f"DBG: MARKET_OPEN_FIRST_SCAN={MARKET_OPEN_FIRST_SCAN} is_market_open={is_market_open()}")
                if MARKET_OPEN_FIRST_SCAN and is_market_open():
                    # Clear the module-level flag explicitly. Using globals() avoids
                    # accidentally creating a local variable in this function scope
                    # which would leave the module-level flag unchanged.
                    globals()["MARKET_OPEN_FIRST_SCAN"] = False
                    logger.info("🔔 First market scan completed - enabling normal heartbeat checks")
            except Exception:
                pass

            # Always show Top-5 by display score (highest->lowest). Annotate heartbeat reason.
            for item in sorted(scored_tickers, key=lambda x: _compute_display_score_from_row(x), reverse=True)[:5]:
                display_score = _compute_display_score_from_row(item)
                hb = item.get('heartbeat')
                logger.info("   " + _format_full_labels(item, hb, display_score))

        except Exception as e:
            logger.error(f"❌ Monitoring error: {e}")

        # Dynamic cadence (will be 5 min during market hours)
        cadence = get_scan_cadence_minutes()
        sleep_seconds = cadence * 60

        # SMART SLEEP: If we haven't made a pick yet and 09:50 is coming up,
        # wake up at 09:50 instead of sleeping the full cadence
        if not pick_made:
            now = datetime.now(pytz.timezone("America/New_York"))
            sel_window = (cfg.get("open_selection", {}) or {}).get("selection_window", "09:50-09:55")
            try:
                start_s, _ = sel_window.split("-")
                start_t = datetime.strptime(start_s.strip(), "%H:%M").time()
            except Exception:
                start_t = datetime.strptime("09:50", "%H:%M").time()

            # Calculate target time for pick (09:50 today)
            pick_time = pytz.timezone("America/New_York").localize(
                datetime.combine(now.date(), start_t)
            )
            seconds_until_pick = (pick_time - now).total_seconds()

            # If pick time is within the next sleep cycle, adjust sleep to wake at pick time
            if 0 < seconds_until_pick <= sleep_seconds:
                sleep_seconds = max(60, int(seconds_until_pick))  # At least 60 seconds
                logger.info(f"⏰ Adjusting sleep to {sleep_seconds/60:.1f} min to wake at {start_t.strftime('%H:%M')} for pick selection")

        # WRAP SLEEP: record planned wake and detect overshoot. If we overshoot by
        # >60s, log a warning and trigger immediate re-check (fallback).
        planned_wake = time.monotonic() + sleep_seconds
        try:
            time.sleep(sleep_seconds)
        except Exception as e:
            logger.error(f"❌ Sleep interrupted: {e}", exc_info=True)
        finally:
            actual = time.monotonic()
            if actual > planned_wake + 60:
                logger.warning(f"⚠️ Sleep overshot planned wake by {actual - planned_wake:.1f}s; forcing immediate re-evaluation")
                # Immediate re-evaluation: continue the loop to pick up snapshots
                continue
        

    logger.info("Scanner stopped.")

# -------------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    cfg = load_config()

    # Phase 1: optional scheduler dry-run (no-op unless SCHEDULER_DRY_RUN is set)
    maybe_run_scheduler_dry_run()

    # Seed CSV at start for both modes
    ensure_today_pick_ready(cfg, reset=True)

    if "--once" in sys.argv:
        logger = setup_logger(cfg["output"]["log"])
        force = "--final-pick-now" in sys.argv
        try:
            run_once(cfg, logger, force_final_pick=force)
        except Exception as e:
            logger.error(f"❌ Error in --once run: {e}", exc_info=True)
    else:
        # IMPORTANT: do NOT set up logger here; run() will do it
        run()

