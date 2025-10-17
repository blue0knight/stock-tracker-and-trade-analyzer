"""Dry-run integration harness for Phase 5: Scheduler -> Router -> System2

This script simulates two scheduler-emitted state transitions and invokes
scanner integration hooks for system1 and system2 in dry-run mode.

It avoids external API calls by providing minimal context and by relying on
internal dry-run stubs (system1/system2 are import-safe).
"""
from datetime import datetime
import logging
from src.scanner import scanner

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dryrun")

def simulate():
    # Simulate PREMARKET
    state = "PREMARKET"
    log.info("[SCHEDULER] -> State changed: %s", state)
    print(f"[SCHEDULER] -> State changed: {state}")
    # Router: call system1 via existing helper
    context = {"now": datetime.now(), "dry_run": True}
    log.info("🧭 [Router] state=%s -> System1", state)
    print(f"🧭 [Router] state={state} -> System1")
    alerts = scanner._invoke_system1_if_applicable({"systems": {"system1": {"enabled": True}}}, None, context)
    log.info("🚀 [System1] produced %d alerts (dry-run)", len(alerts))
    print(f"🚀 [System1] produced {len(alerts)} alerts (dry-run)")

    # Simulate picking window state
    state = "PICK_WINDOW"
    log.info("[SCHEDULER] -> State changed: %s", state)
    print(f"[SCHEDULER] -> State changed: {state}")
    log.info("🧭 [Router] state=%s -> System2", state)
    print(f"🧭 [Router] state={state} -> System2")
    # Provide a simple enriched context with two candidate tickers
    enriched = [
        {"ticker": "AAA", "last_price": 5.0, "intraday_volume": 2000, "rvol": 1.5, "gap_pct": 10.0, "atr_stretch": 1.0},
        {"ticker": "BBB", "last_price": 6.0, "intraday_volume": 1500, "rvol": 1.2, "gap_pct": 8.0, "atr_stretch": 1.0},
    ]
    context2 = {"enriched": enriched, "system1_alerts": [], "dry_run": True}
    # Temporarily create a config dict enabling system2 for this dry-run
    cfg = {"systems": {"system2": {"enabled": True, "max_picks": 3, "min_price": 1.0, "min_intraday_volume": 0, "min_rvol": 0.0, "min_score": 0.0, "priority_alerts_only": False}}}
    picks = scanner._invoke_system2_if_applicable(cfg, None, context2)
    log.info("🎯 [System2] Pick Generator scaffold active (dry-run=%s); picks=%d", True, len(picks))
    print(f"🎯 [System2] Pick Generator scaffold active (dry-run=True); picks={len(picks)}")
    for p in picks:
        print("   PICK:", p.get("ticker"), "score=", p.get("pick_score"))

    log.info("✅ Pick simulation complete.")
    print("✅ Pick simulation complete.")

if __name__ == '__main__':
    simulate()
