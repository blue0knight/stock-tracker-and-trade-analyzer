# 📜 **CHANGELOG.md**

## [Phase 3 — architecture-implementation] — 2025-10-16
### Added
  - Introduced `MarketTimestamp` and `SimulationState` for timestamp handling and pause detection.
  - Added deterministic unit tests (`tests/test_data_model.py`).
  - Implemented opt-in simulation bootstrap in `scanner.py` controlled by `SCANNER_SIM_MODE`.
  - Added `simulation:` config block in `scanner.yaml`.

### Changed

### Notes



## [Phase 5 — system2-pick-generator] — 2025-10-17
### Added
- **System 2: Pick Generator (Phase 5)**
  - Implemented `src/systems/system2.py`: deterministic pick generator using normalized features and optional alert bonuses.
  - Added `src/core/pick_utils.py` with `compute_pick_score()` and `_rank_candidates()` for deterministic scoring and ranking.
  - Introduced `tests/test_system2.py` covering scoring, filtering, and alert-prioritization (deterministic unit tests).
  - Config keys added to `configs/systems.yaml` to control `max_picks`, `min_price`, `min_intraday_volume`, `min_rvol`, `min_score`, and `priority_alerts_only`.

### Changed
- None (additive-only; dry-run safe)

### Notes
- All new code is dry-run safe and covered by deterministic unit tests. No external network calls or side-effects are introduced.

## [Phase 2 — architecture-implementation] — 2025-10-15
### Added
- **Systems Scaffolding**
  - Created `System1` (Explosive Alerts) and `System2` (Liquidity Triggers) stub classes.
  - Added dry-run safe initialization routines and config handling.
- **Systems Validation**
  - Integrated routing tests for premarket and pick-window states.
- **Governance**
  - Added test harness for PREMARKET and PICK_WINDOW validation routines.

### Notes
- No API or runtime logic; purely import-safe scaffolds for routing verification.

---

## [Phase 1 — architecture-implementation] — 2025-10-14
### Added
- **System Architecture Bootstrap**
  - Introduced the `systems/` package and validation scaffolds (`system1.py`, `system2.py`, `systems.yaml`).
  - Implemented dry-run validator in `scanner.py` (`validate_systems()`).
- **Scheduler State Framework**
  - Added base state logic for `PREMARKET` and `PICK_WINDOW`.
- **Governance Integration**
  - Established phased rollout protocol and validation checkpoints.

### Notes
- No runtime logic; foundational scaffolding only.

---

## [hotfix/v0.4.2] — 2025-10-16
### Added
- **Heartbeat:** Hardened one-time market-open allowance so the scanner only awards a single first-scan heartbeat bypass at open. Added runtime diagnostics (`MARKET_OPEN_FIRST_SCAN`, `MARKET_OPEN_FIRST_SCAN_USED`) and clearer logging around allowance consumption.
- **Smart-Sleep:** Fixed sleep drift/overshoot handling by using monotonic planned wake times and adding defensive wake/overshoot logging so long sleeps trigger immediate re-evaluation instead of silently waiting.
- **Deficiency Filter:** Throttled expensive historical/deficiency checks to the top-N candidates and added a per-call timeout (fail-open) to avoid blocking the selection path.

---

## ✅ **Protocol Reminder**
- Each **Phase** must include: `Added`, `Changed`, and `Notes` sections.  
- Each **Hotfix** must specify affected subsystems and scope.  
- Order: **newest → oldest**.  
- Commit changelog updates together with feature or release tags.

---

### 🧱 **Commit Instructions**

```bash
git add CHANGELOG.md
git commit -m "docs: add Phase 1–3 entries (architecture-implementation) and enforce changelog protocol"
git push
git tag -a v3.0.0-phase3 -m "Phase 3: DataDelayModel & Simulation Layer (architecture-implementation)"
git push origin v3.0.0-phase3
```
