# -------------------------------------------------------------------
# FILE: src/adapters/polygon_adapter.py
# DATE: 2025-09-29
# PURPOSE: Polygon adapter for snapshots, premarket high, ATR(14),
#          intraday volume, average daily volume, and 09:30 open price.
# NOTES:
#   - Requires POLYGON_API_KEY in environment or .env
#   - All network calls have basic error handling & timeouts
#   - Timezone-aware calculations (America/New_York)
# -------------------------------------------------------------------
from __future__ import annotations

import os
import calendar
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

import pytz
import requests

BASE_URL = "https://api.polygon.io"
HTTP_TIMEOUT = 30  # seconds


# ---------------------------- helpers --------------------------------
def _require_api_key() -> str:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("❌ No POLYGON_API_KEY found in environment or .env file")
    return api_key


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "polygon-stock-tracker/1.0"})
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=3))
    return s



def to_unix_ms(dt: datetime) -> int:
    """Convert aware datetime -> unix epoch milliseconds (UTC)."""
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return int(calendar.timegm(dt.utctimetuple()) * 1000)


def _today_ny() -> datetime:
    return datetime.now(pytz.timezone("America/New_York"))


# -------------------------------------------------------------------
# Ticker Status Check (filter delisted/deficient/OTC)
# -------------------------------------------------------------------
# Cache to avoid repeated API calls for same ticker
_TRADEABLE_CACHE = {}
_DEFICIENCY_CACHE = {}

def is_deficient_nasdaq(ticker: str, lookback_days: int = 45, grace_period_days: int = 10) -> tuple[bool, str]:
    """
    Check if ticker is deficient based on NASDAQ $1 minimum bid price rule.

    NASDAQ Rules:
    - Stock below $1.00 for 30+ consecutive business days = deficient
    - After deficiency, must maintain $1+ for grace_period_days to be considered compliant

    Args:
        ticker: Stock symbol
        lookback_days: Days of history to check (default 45)
        grace_period_days: Days stock must stay above $1 after deficiency to clear (default 10)

    Returns:
        (is_deficient: bool, reason: str)
    """
    # Check cache first
    if ticker in _DEFICIENCY_CACHE:
        return _DEFICIENCY_CACHE[ticker]

    try:
        api_key = _require_api_key()
        end = datetime.utcnow().date()
        start = end - timedelta(days=lookback_days)

        url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        params = {"adjusted": "true", "sort": "asc", "limit": lookback_days, "apiKey": api_key}

        with _session() as s:
            resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            bars = resp.json().get("results", []) or []

        if len(bars) < 30:
            # Not enough history, assume OK
            result = (False, "insufficient_history")
            _DEFICIENCY_CACHE[ticker] = result
            return result

        # Analyze price history from most recent to oldest
        # State machine: compliant -> below_threshold -> deficient -> recovery -> compliant

        consecutive_below = 0  # Current streak below $1
        consecutive_above = 0  # Current streak above $1
        was_deficient = False  # Found a deficiency period going backwards

        for bar in reversed(bars):  # Most recent first
            close = float(bar.get("c", 0) or 0)

            if close < 1.0:
                # Below threshold
                consecutive_below += 1
                consecutive_above = 0  # Reset above counter

                # Check if currently deficient
                if consecutive_below >= 30:
                    result = (True, f"deficient_{consecutive_below}d_below_$1")
                    _DEFICIENCY_CACHE[ticker] = result
                    return result
            else:
                # Above threshold
                if consecutive_below >= 30:
                    # Found a deficiency period before current above-threshold streak
                    was_deficient = True

                consecutive_above += 1
                consecutive_below = 0  # Reset below counter

                # If we were deficient and are now above, check grace period
                if was_deficient:
                    if consecutive_above < grace_period_days:
                        # In grace period - still considered deficient
                        result = (True, f"grace_period_{consecutive_above}/{grace_period_days}d")
                        _DEFICIENCY_CACHE[ticker] = result
                        return result
                    else:
                        # Grace period complete - now compliant
                        result = (False, "compliant_post_grace")
                        _DEFICIENCY_CACHE[ticker] = result
                        return result

        # Never found deficiency
        result = (False, "compliant")
        _DEFICIENCY_CACHE[ticker] = result
        return result

    except Exception as e:
        # On error, assume not deficient (don't filter out due to API issues)
        result = (False, f"check_error")
        _DEFICIENCY_CACHE[ticker] = result
        return result


def is_ticker_deficient_fast(ticker: str, timeout: int = 5) -> tuple[bool, str]:
    """
    Run is_deficient_nasdaq with a safety timeout. On timeout or error,
    assume not deficient (do not filter out). This prevents the scanner from
    blocking on slow API calls for many tickers.

    Returns: (is_deficient: bool, reason: str)
    """
    try:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(is_deficient_nasdaq, ticker)
            return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return (False, "timeout_assume_ok")
    except Exception:
        return (False, "error_assume_ok")

def is_ticker_tradeable(ticker: str) -> tuple[bool, str]:
    """
    Check if ticker is tradeable (not delisted, deficient, or OTC).
    Uses in-memory cache to avoid repeated API calls.

    Returns:
        (is_tradeable: bool, reason: str)
    """
    # Check cache first
    if ticker in _TRADEABLE_CACHE:
        return _TRADEABLE_CACHE[ticker]

    api_key = _require_api_key()
    url = f"{BASE_URL}/v3/reference/tickers/{ticker}"

    try:
        resp = requests.get(url, params={"apiKey": api_key}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "OK":
            return False, "ticker_not_found"

        results = data.get("results", {})

        # Check market (must be stocks, not OTC)
        market = results.get("market", "").lower()
        if market == "otc":
            result = (False, "otc_market")
            _TRADEABLE_CACHE[ticker] = result
            return result

        # Check if active
        active = results.get("active", False)
        if not active:
            result = (False, "inactive")
            _TRADEABLE_CACHE[ticker] = result
            return result

        # Check type (must be common stock CS, not ADR/warrant/etc)
        ticker_type = results.get("type", "").upper()
        if ticker_type in ["ADRC", "WARRANT", "RIGHT", "UNIT"]:
            result = (False, f"type_{ticker_type.lower()}")
            _TRADEABLE_CACHE[ticker] = result
            return result

        # Check delisted flag
        delisted = results.get("delisted_utc")
        if delisted:
            result = (False, "delisted")
            _TRADEABLE_CACHE[ticker] = result
            return result

        result = (True, "tradeable")
        _TRADEABLE_CACHE[ticker] = result
        return result

    except requests.Timeout:
        # On timeout, assume tradeable (don't filter out due to API issues)
        result = (True, "timeout_assume_ok")
        _TRADEABLE_CACHE[ticker] = result
        return result
    except Exception as e:
        # On error, assume tradeable (don't filter out due to API issues)
        result = (True, f"error_assume_ok")
        _TRADEABLE_CACHE[ticker] = result
        return result


# ------------------------- snapshots adapter --------------------------
def fetch_snapshots(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch latest snapshot data for US stocks from Polygon v3 API.
    Supports premarket/extended hours pricing.
    Returns a list of dicts with ticker + basic prices.
    """
    api_key = _require_api_key()
    url = f"{BASE_URL}/v3/snapshot"

    # v3 uses pagination; fetch up to 'limit' tickers
    out: List[Dict[str, Any]] = []
    next_url = None
    batch_size = min(250, limit)  # v3 max is 250 per request

    params = {"ticker.gte": "A", "limit": batch_size, "apiKey": api_key}

    with _session() as s:
        while len(out) < limit:
            if next_url:
                # Append API key to next_url (pagination URLs don't include it)
                sep = "&" if "?" in next_url else "?"
                resp = s.get(f"{next_url}{sep}apiKey={api_key}", timeout=HTTP_TIMEOUT)
            else:
                resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)

            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", []) or []
            for r in results:
                session = r.get("session", {}) or {}
                out.append({
                    "ticker": r.get("ticker"),
                    "last_price": session.get("price"),
                    "prev_close": session.get("previous_close"),
                    "volume": session.get("volume"),
                    "open": session.get("open"),
                    "high": session.get("high"),
                    "low": session.get("low"),
                    "close": session.get("close"),
                })

                if len(out) >= limit:
                    break

            # Check for pagination
            next_url = data.get("next_url")
            if not next_url or len(out) >= limit:
                break

    return out[:limit]


## -------------------------------------------------------------------
## BLOCK: get-snapshot-single  |  FILE: src/adapters/polygon_adapter.py  |  DATE: 2025-10-01
## PURPOSE: Fetch single-ticker snapshot (for hybrid enrichment)
## NOTES:
##   - Uses v3 API for extended hours support
##   - Returns dict compatible with bulk fetch_snapshots shape
## -------------------------------------------------------------------
def get_snapshot(ticker: str) -> Dict[str, Any]:
    api_key = _require_api_key()
    url = f"{BASE_URL}/v3/snapshot"

    with _session() as s:
        resp = s.get(url, params={"ticker.any_of": ticker, "apiKey": api_key}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json() or {}

    results = data.get("results", [])
    if not results:
        return {}

    r = results[0]
    session = r.get("session", {}) or {}

    return {
        "ticker": r.get("ticker"),
        "last_price": session.get("price"),
        "prev_close": session.get("previous_close"),
        "volume": session.get("volume"),
        "open": session.get("open"),
        "high": session.get("high"),
        "low": session.get("low"),
        "close": session.get("close"),
    }
## -------------------------------------------------------------------


# --------------------------- previous close ---------------------------
def get_previous_close(ticker: str) -> float:
    api_key = _require_api_key()
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev"
    params = {"adjusted": "true", "apiKey": api_key}

    with _session() as s:
        resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results", []) or []

    return results[0].get("c", 0.0) if results else 0.0


# --------------------------- premarket high ---------------------------
def get_premarket_high(ticker: str, date: str) -> float:
    """
    Highest price between 04:00:00 and 09:29:59 ET for the given trade date.
    Uses 1-minute aggregates with UNIX ms range.
    """
    api_key = _require_api_key()
    ny = pytz.timezone("America/New_York")
    start_dt = ny.localize(datetime.strptime(f"{date} 04:00:00", "%Y-%m-%d %H:%M:%S"))
    end_dt = ny.localize(datetime.strptime(f"{date} 09:29:59", "%Y-%m-%d %H:%M:%S"))

    start_ms = to_unix_ms(start_dt.astimezone(pytz.UTC))
    end_ms = to_unix_ms(end_dt.astimezone(pytz.UTC))

    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{start_ms}/{end_ms}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}

    with _session() as s:
        resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        bars = resp.json().get("results", []) or []

    highs = [bar.get("h", 0.0) for bar in bars]
    return max(highs) if highs else 0.0


# ------------------------------ ATR(14) -------------------------------
def get_atr_14(ticker: str, trade_date: str) -> float:
    """
    Compute 14-day ATR using Polygon daily bars up to `trade_date`.
    Falls back to 1.0 if insufficient data or on error.
    """
    try:
        api_key = _require_api_key()
        end = datetime.strptime(trade_date, "%Y-%m-%d")
        start = end - timedelta(days=40)  # generous buffer for 14 obs

        url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
        params = {"adjusted": "true", "sort": "asc", "limit": 200, "apiKey": api_key}

        with _session() as s:
            resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            bars = resp.json().get("results", []) or []

        if len(bars) < 15:
            return 1.0  # not enough history

        trs: List[float] = []
        for i in range(1, len(bars)):
            high = float(bars[i].get("h", 0.0) or 0.0)
            low = float(bars[i].get("l", 0.0) or 0.0)
            prev_close = float(bars[i - 1].get("c", 0.0) or 0.0)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        atr = sum(trs[-14:]) / 14
        return round(atr, 4) if atr > 0 else 1.0
    except Exception:
        return 1.0


# --------------------------- intraday volume --------------------------
def get_intraday_volume(ticker: str, trade_date: str) -> int:
    """
    Return cumulative intraday volume from 04:00 ET up to 'now' ET for trade_date.
    Uses 1-minute aggregates; sums 'v' across bars.
    Falls back to 0 on error or no data.
    """
    try:
        api_key = _require_api_key()
        ny = pytz.timezone("America/New_York")

        start_dt = ny.localize(datetime.strptime(f"{trade_date} 04:00:00", "%Y-%m-%d %H:%M:%S"))
        now_ny = _today_ny()
        # If you're querying a past date intraday volume, cap at that day's 20:00 to avoid future bars
        end_dt = now_ny if trade_date == now_ny.strftime("%Y-%m-%d") else ny.localize(
            datetime.strptime(f"{trade_date} 20:00:00", "%Y-%m-%d %H:%M:%S")
        )

        start_ms = to_unix_ms(start_dt.astimezone(pytz.UTC))
        end_ms = to_unix_ms(end_dt.astimezone(pytz.UTC))

        url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{start_ms}/{end_ms}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}

        with _session() as s:
            resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            bars = resp.json().get("results", []) or []

        return int(sum(int(bar.get("v", 0) or 0) for bar in bars))
    except Exception:
        return 0


# ------------------------ average daily volume ------------------------
def get_avg_daily_volume(ticker: str, lookback: int = 20) -> int:
    """
    Return average daily volume over the most recent `lookback` sessions.
    Falls back to 0 on error or if no data.
    """
    try:
        api_key = _require_api_key()
        end = datetime.utcnow().date()
        start = end - timedelta(days=max(lookback * 3, 90))  # buffer for market-closed days

        url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        params = {"adjusted": "true", "sort": "desc", "limit": lookback, "apiKey": api_key}

        with _session() as s:
            resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            bars = resp.json().get("results", []) or []

        if not bars:
            return 0

        vols = [int(bar.get("v", 0) or 0) for bar in bars[:lookback]]
        return int(sum(vols) / len(vols)) if vols else 0
    except Exception:
        return 0


# --------------------------- 09:30 open price -------------------------
def get_open_price_0930(ticker: str, trade_date: str) -> float:
    """
    Fetch the true 09:30:00 ET open price for `trade_date` using 1-min bars.
    If the 09:30 bar is missing, returns 0.0.
    """
    api_key = _require_api_key()
    ny = pytz.timezone("America/New_York")

    start_dt = ny.localize(datetime.strptime(f"{trade_date} 09:30:00", "%Y-%m-%d %H:%M:%S"))
    end_dt = ny.localize(datetime.strptime(f"{trade_date} 09:31:00", "%Y-%m-%d %H:%M:%S"))

    start_ms = to_unix_ms(start_dt.astimezone(pytz.UTC))
    end_ms = to_unix_ms(end_dt.astimezone(pytz.UTC))

    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{start_ms}/{end_ms}"
    params = {"adjusted": "true", "sort": "asc", "limit": 5, "apiKey": api_key}

    with _session() as s:
        resp = s.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        bars = resp.json().get("results", []) or []

    if not bars:
        return 0.0

    # The first bar in this window should be 09:30
    first_bar = bars[0]
    return float(first_bar.get("o", 0.0) or 0.0)
