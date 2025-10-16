## [hotfix/v0.4.2] - 2025-10-16
### Added
- Heartbeat: Harden one-time market-open allowance so the scanner only awards a single first-scan heartbeat bypass at open. Added runtime diagnostics (MARKET_OPEN_FIRST_SCAN, MARKET_OPEN_FIRST_SCAN_USED) and clearer logging around allowance consumption.
- Smart-Sleep: Fixed sleep drift/overshoot handling by using monotonic planned wake times and adding defensive wake/overshoot logging so long sleeps trigger immediate re-evaluation instead of silently waiting.
- Deficiency Filter: Throttled expensive historical/deficiency checks to the top-N candidates and added a per-call timeout (fail-open) to avoid blocking the selection path.

## [dev/premarket-test] - 2025-10-08
### Added
- **End-of-Day Analysis System** (`scripts/analyze_eod.py`)
  - Analyzes scanner logs to identify best catchable picks (>8% gain)
  - Calculates optimal entry/exit windows with specific times and prices
  - Generates daily reports: `output/reports/eod_report_YYYY-MM-DD.md`
  - Appends top 5 picks to `output/missed.csv` for historical tracking
  - Compares actual trades (from journal.csv) vs. best picks
  - Shows hit rate and efficiency (% of max gain captured)
  - Usage: `python scripts/analyze_eod.py`

- **Quick Trade Logger** (`scripts/log_today_trades.py`)
  - Interactive CLI to quickly log trades to journal.csv
  - Calculates P&L automatically
  - Usage: `python scripts/log_today_trades.py`

- **File Reorganization**
  - Created `docs/` folder for all documentation
  - Created `output/reports/` for daily generated reports
  - Moved CHANGELOG.md, SYSTEM_GUIDE.md, EOD_ANALYSIS.md to `docs/`
  - Moved patch notes to `docs/archive/`

- **Comprehensive Documentation**
  - `docs/SYSTEM_GUIDE.md` - Complete daily workflow guide
  - `docs/EOD_ANALYSIS.md` - End-of-day analysis documentation
  - Updated README.md with new structure and EOD workflow

- **Dependencies** (`requirements.txt`)
  - Now tracked in git for reproducibility

### Changed
- **🚨 CRITICAL FIX: Heartbeat Calculation**
  - **Before**: Used `(current_price - last_scan_price)` / last_scan_price
  - **After**: Uses `(current_price - market_open_price_930am)` / market_open_price_930am
  - **Impact**: Now catches slow climbers that gain 8-15% over 2-3 hours
  - **Example**: RXRX (+15.8%), IONZ (+10.3%), ACHR (+8.7%) would now be detected
  - **Location**: `src/scanner/scanner.py:174-250`

- **Pick Timing Adjustment**
  - Changed from 9:30-9:35 AM → **9:50-9:55 AM**
  - Allows 20 min for price action confirmation
  - Reduces false picks on extended pumps

- **Liquidity Filters** (configs/scanner.yaml)
  - Regular session: Lowered thresholds for small-cap plays
    - min_intraday_shares: 100k → 50k
    - min_avg_daily_volume: 500k → 100k
    - min_dollar_volume: $300k → $50k
  - Maintains $1.00 minimum for NASDAQ compliance

- **Project Structure**
  - Cleaned up: Removed `venv/`, `data/`, `__pycache__/`, `.DS_Store`
  - Updated .gitignore: Added `venv/`, `output/reports/`, `docs/archive/`

### Performance Data (10/08)
- **Scanner Pick**: XBIO (+180% gap) - Not tradeable (overextended)
- **User Pick**: CIFR @ $15.30 → $17.60 (+15.0%, +$149.50) ✅
- **EOD Analysis**: CIFR was pick #3, user captured 98% of max gain
- **Best Picks Found**:
  1. UPC (+32.5%) - Entry: 9:41-10:07 @ $7.54-$8.27
  2. RXRX (+15.8%) - Entry: 10:50-11:06 @ $5.79-$5.83
  3. CIFR (+15.3%) ✅ - **User got this!**
  4. IONZ (+10.3%) - Entry: 10:24-10:33 @ $2.98-$3.04
  5. ACHR (+8.7%) - Entry: 12:52-13:18 @ $12.05-$12.39

---

## [dev/premarket-test] - 2025-10-03
### Added
- **Trade Performance Tracking** (`scripts/log_trade.py`)
  - Quick CLI tool to log entries/exits to journal.csv
  - Track actual trades vs scanner picks
  - Usage: `python scripts/log_trade.py TICKER 1.05 1.25 --shares 100`
- **Scan History Analyzer** (`src/analysis/scan_analyzer.py`)
  - Extract insights from scanner logs (when did winners first appear?)
  - Identify best performers and optimal entry timing
  - Analyze individual ticker patterns
  - Usage: `python -m src.analysis.scan_analyzer logs/scanner_2025-10-03.log DFLI`
- **Manual Pick Override** (`scripts/manual_pick.py`)
  - Override automated pick when top choice is not tradeable
  - Show current picks from today_pick.csv
  - Usage: `python scripts/manual_pick.py SOPA` (if SPRB not tradeable)

### Changed
- **🚨 CRITICAL FIX**: Composite scoring formula now properly rewards high-volume runners
  - Reduced gap% weight: 25% → 20% (gap alone insufficient)
  - Increased volume weight: 25% → 30% (volume matters!)
  - Added absolute volume bonus (logarithmic scale)
  - **Impact**: DFLI (391M vol) now ranks above XELB (12M vol) correctly
  - Fixes issue where high-volume winners were buried in Top 5
- **Dynamic Scan Cadence**: Scanner now adjusts frequency based on time
  - 4:00-9:00 AM: 30 min (early premarket)
  - 9:00-9:15 AM: 15 min (pre-open ramp)
  - 9:15-9:30 AM: 5 min (final approach)
  - 9:30+ AM: 5 min (market hours)
  - Fixes issue where 9:15-9:30 was scanning every 30 min instead of 5 min
- **NASDAQ Compliance**: Now requires BOTH price >= $1 AND prev_close >= $1
  - Prevents APLT-type issues (gapping up from sub-$1 prev_close)
  - Stricter deficiency filtering

### Fixed
- CSV header in `today_pick_template.csv` now single line (was split across 2 lines)
- **MSTR false positive**: Improved suffix filter to avoid flagging legitimate tickers ending in R
- **Market open Top 5 empty**: Heartbeat now uses fallback history during tight windows (fixes 9:30 AM transition)

### Performance Data (10/02-10/03)
- **10/02**: TSHA @ $4.68 → $4.75 (+1.5%) - Late entry (first scan: $4.22, peak: $4.66)
- **10/03**: RELI @ $1.05 - Good timing (holding)
- **10/03**: DFLI appeared 34 times, ran $1.29 → $2.15 (+66.7%) - Proves scanner works!

---

## [dev/final-pick] - 2025-09-27
### Added
- `today_pick.csv` single-file schema with FinalPick flag and rationale.
- New schema files: `schemas/today_pick.schema.json` and `schemas/today_pick_template.csv`.
- ATR-14 calculation and enrichment in `polygon_adapter.py`.
- Debug mode toggle in `scanner.yaml` (injects dummy ticker on weekends/testing).
- Auto-seeding of CSV headers from template on scanner startup.

### Changed
- Updated `scanner.py` to enrich rows with ATR, gap %, RVOL, ATR stretch, rationale.
- Centralized Final Pick row appending via `_final_pick_row()` and `append_row()`.
- Liquidity filter logic clarified (drops logged by category).


## [dev/final-pick] - 2025-09-27
### Added
- Liquidity filters (configurable via `scanner.yaml`):
  - min_intraday_shares
  - min_avg_daily_volume
  - min_dollar_volume
  - min_price
  - require_prices
- Current Pick logging:
  - Logs the current strongest candidate each cycle (dry-run mode).
  - Distinguishes from Final Pick at 09:50 ET.
- Simulation override (prep work) for running past dates.

### Fixed
- Enrichment pipeline now correctly processes snapshot lists (no more string index errors).
- Guarded scoring + watchlist writing (no more `NameError: scored not defined`).
- Consistent flow in both main loop and `--once` path: snapshots → enrich → filter → score.
- Liquidity config now loads correctly from YAML (no fallback to min_price=50).

### QA / Protocol
- Validated scanner runs with liquidity filter + logging without crashes.
- Weekend runs drop all tickers (expected due to no live data).
- Monday premarket runbook documented and ready.

---


## [0.3.1] – 2025-09-25
### Fixed
- Added `.env` loading in `scanner.py`

## [0.3.0] – 2025-09-21
### Added
- Introduced `src/core/journal.py` for recording trades into `journal.csv`.
- Added `schemas/journal_template.csv` with clean headers for paper-trading.
- Added `scripts/analyze_week.py` to summarize trades and generate weekly reports.
- Updated `README.md` with full repo structure and usage instructions.

## [0.2.4] – 2025-09-21
### Fixed
- `score_snapshots()` now supports Polygon’s `prev_close` / `last_price` fields and falls back to `todays_change_pct`.
- Top-5 movers now correctly scored and written into `output/watchlist.csv`.


## [0.2.3] – 2025-09-21
### Added
- `src/core/output.py`: helper to write Top-5 movers into `output/watchlist.csv` with schema headers.
- Integrated `write_watchlist()` into both scanner loop and `--once` dry-run.
- Appends targets (`t1`, `t2`, `stretch`) from `scanner.yaml`.

### Changed
- Updated `src/scanner/scanner.py` to handle snapshots as lists, convert to dict, and log + write Top-5 movers.


## [0.2.2] – 2025-09-21
### Added
- `src/core/scoring.py`: Scoring module with gap % calculation, Top-5 ranking, and logging helpers.
- `tests/test_scoring.py`: Unit tests validating gap %, ranking, and edge cases.

### Changed
- Integrated scoring into `src/scanner/scanner.py` (`--once` dry-run and loop mode now log Top-5 movers).

## [0.2.1] – 2025-09-21
### Added
- `src/adapters/polygon_adapter.py`: Polygon API adapter for snapshot fetching.
- `src/scanner/scanner.py`: added `--once` dry-run mode to fetch all tickers (~11,700) and log results.
- `scripts/test_fetch.py`: standalone script to validate Polygon connectivity.

### Changed
- Minor logging improvements in scanner for dry-run mode.

## [0.2.0] – 2025-09-21
### Added
- Introduced `src/scanner/scanner.py` with the initial premarket scanner loop skeleton:
  - Loads configuration from `scanner.yaml`.
  - Sets up logging to `logs/scanner.log`.
  - Runs a loop between configured `start_time` and `end_time`.
- Extended `scanner.yaml` with:
  - `output` block (paths for watchlist, missed, logs, schema).
  - `pump_prone` block (earliest_entry, time_stop).
  - `targets` block (T1, T2, stretch levels).

## [0.1.3] – 2025-09-21
### Added
- Added `schemas/watchlist_template.csv` and `schemas/missed_template.csv` with clean headers and working Excel/Sheets formulas.
  - `watchlist_template.csv`: auto-calculates `total_cost`, P/L $, and P/L %; falls back to T1 target if no exit price.
  - `missed_template.csv`: auto-calculates `total_cost`, P/L $, and P/L %; falls back to exit_target if no close price.




