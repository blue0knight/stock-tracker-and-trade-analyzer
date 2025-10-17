# 📜 **CHANGELOG.md**

## [Unreleased] Phase 7 — Tests & Release (October 2025)

### Added
- **CI Automation** – `.github/workflows/ci.yml` for syntax, test, and coverage verification  
- **Development Requirements** – `requirements-dev.txt` including `pytest`, `coverage`, `pytest-cov`, and `httpx`  
- **Test Fixtures & Integration Tests**
  - `tests/conftest.py` → environment isolation + fake `POLYGON_API_KEY`
  - `tests/test_integration_systems.py` → dry-run validation with diagnostics enabled
- **Developer Documentation** – expanded `README.md` with Phase 7 Developer Guide (tests, coverage, dry-run, CI, governance summary)
- **Governance Artifacts**
  - Coverage reports: `coverage.xml`, `htmlcov/`
  - DIAG snapshot verification confirming diagnostics default = OFF
  - Governance validation summary table included in `README.md`

### Changed
- **Legacy Test Isolation** – marked `tests/test_scoring.py` as skipped (legacy placeholder)  
- **Test Path Corrections** – unified imports under `src.*` structure  
- **Documentation Updates** – integrated coverage and CI instructions in README

### Coverage Snapshot
| Metric | Result | Notes |
|:--|:--:|:--|
| Total coverage | **34 %** | Automated via `pytest-cov` |
| Core modules | **≥ 80 %** | `src/core/*`, `src/systems/*` |
| Diagnostics default | ✅ | Verified OFF |
| Integration tests | ✅ | 15 passed / 1 skipped (legacy) |

### Release Candidate Plan
- **Branch:** `release/rc-7.0.0-rc1`  
- **Tag:** `v7.0.0-rc1` → annotated pre-release  
- **Final Tag:** `v7.0.0` after RC validation and sign-off  
- **Commit References:**
  - `2f892f3` – CI workflow  
  - `a8be5c0` – README update (Phase 7 Developer Guide)
  - [phase 7 test/fixture commits as validated earlier]

### Validation Summary
- ✅ Syntax / Build Checks  
- ✅ All Tests Passing (1 skip legacy)  
- ✅ Coverage Artifacts Generated  
- ✅ Diagnostics Stable / Default OFF  
- ✅ Governance Docs Complete  


## [Phase 6 — Hardening & Diagnostics] — 2025-10-17
### Added
- **Centralized Logging Configuration**
  - Introduced `src/core/logging_config.py` with idempotent `configure_logging()` setup (console + file handlers, safe fallback to console-only).  
  - Added `configs/logging.yaml` (planned) for log-level and file-path control.  

- **Diagnostics Framework**
  - Created `src/core/diagnostics.py` providing lightweight metrics (counters + timers).  
  - Global singleton `DIAG` (disabled by default) allows optional instrumentation without runtime overhead.  

- **Instrumentation**
  - Integrated `configure_logging()` and `DIAG` hooks into `scanner.py` for safe event/time tracking on System 2 invocations.  
  - Added `tests/test_diagnostics.py` (metrics + timing) and `tests/test_tiebreak.py` (deterministic ordering).  

### Changed
- None (additive-only, dry-run-safe).

### Notes
- Diagnostics disabled by default; enable via `DIAG.enabled = True` or config toggle for local validation.  
- Logging setup is idempotent and fault-tolerant; repeated calls are no-ops.  
- All Phase 6 commits validated via `py_compile`, unit tests, and dry-run integration.


## [Phase 5 — System 2 Pick Generator] — 2025-10-17
### Added
- **System 2: Pick Generator**
  - Implemented `src/systems/system2.py`: deterministic candidate pick generator using normalized market features and optional System 1 alert bonuses.  
  - Added `src/core/pick_utils.py` with `compute_pick_score()` and `_rank_candidates()` helpers for scoring and ranking.  
  - Introduced `tests/test_system2.py` covering scoring, filtering, and alert-prioritization logic (deterministic and network-free).  
  - Extended `configs/systems.yaml` with System 2 configuration keys:  
    `max_picks`, `min_price`, `min_intraday_volume`, `min_rvol`, `min_score`, `priority_alerts_only`.

### Changed
- None (additive-only; dry-run safe)

### Notes
- All code is additive, dry-run safe, and covered by deterministic unit tests.  
- No external network calls or side-effects introduced.  
- Integrated via `_invoke_system2_if_applicable()` hook in `scanner.py` (guarded and optional).

---

## [Phase 4 — System 1 Explosive Alerts] — 2025-10-16
### Added
- **System 1: Explosive Alerts**
  - Implemented `src/systems/system1.py` with dry-run-safe alert logic for early-momentum candidates.  
  - Added `_invoke_system1_if_applicable()` guarded hook in `scanner.py`.  
  - Created lightweight test coverage for initialization and logging behavior.

### Changed
- Logger improvements for visibility during dry-run.  
- Hardened test routines and dry-run reporting.

### Notes
- Foundation for System 2 prioritization.  
- Reversible via single-commit rollback.

---

## [Phase 3 — Architecture Implementation] — 2025-10-16
### Added
- Introduced `MarketTimestamp` and `SimulationState` for timestamp handling and pause detection.  
- Added deterministic unit tests (`tests/test_data_model.py`).  
- Implemented opt-in simulation bootstrap in `scanner.py` controlled by `SCANNER_SIM_MODE`.  
- Added `simulation:` config block in `scanner.yaml`.

### Changed
- None

### Notes
- Deterministic simulation layer verified with controlled test timing.

---

## [Phase 2 — Architecture Implementation] — 2025-10-15
### Added
- **Systems Scaffolding**
  - Created `System1` (Explosive Alerts) and `System2` (Liquidity Triggers) stub classes.  
  - Added dry-run-safe initialization routines and config handling.  
- **Systems Validation**
  - Integrated routing tests for PREMARKET and PICK_WINDOW states.  
- **Governance**
  - Added test harness for PREMARKET and PICK_WINDOW validation routines.

### Notes
- No runtime logic; purely import-safe scaffolds for routing verification.

---

## [Phase 1 — Architecture Implementation] — 2025-10-14
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
- **Heartbeat:** Hardened one-time market-open allowance so the scanner only awards a single first-scan heartbeat bypass at open.  
- **Smart-Sleep:** Fixed sleep drift/overshoot handling by using monotonic planned wake times and adding defensive wake/overshoot logging.  
- **Deficiency Filter:** Throttled expensive historical checks and added per-call timeout (fail-open) to prevent blocking.

---

## ✅ **Protocol Reminder**
- Each **Phase** must include: `Added`, `Changed`, and `Notes`.  
- Each **Hotfix** must specify affected subsystems and scope.  
- Order: **newest → oldest**.  
- Commit changelog updates together with feature or release tags.

---

## 🧱 **Commit & Tag Instructions**

\`\`\`bash
git add CHANGELOG.md
git commit -m "docs: finalize Phase 1–5 entries (architecture-implementation) and enforce changelog protocol"
git push
# optional checkpoint tag
git tag -a v5.0.0-phase5 -m "Phase 5: System 2 Pick Generator (architecture-implementation)"
git push origin v5.0.0-phase5
\`\`\`
