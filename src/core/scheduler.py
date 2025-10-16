#!/usr/bin/env python3
"""Scheduler: Phase 1 cadence engine (dry-runable)

Purpose:
- Provide an ET-aware scheduler that emits planned scan times for the trading day.
- Dry-run writes dual output: stdout + logs/scheduler_dryrun.log
- Keeps simple, YAML-driven hooks available via config (data_delay_minutes)

This is intentionally minimal and reversible. It does not change scanning logic.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from pathlib import Path
import pytz
from typing import List

LOG = logging.getLogger("scheduler")

class Scheduler:
    def __init__(self, config: dict | None = None, tz_name: str = "America/New_York") -> None:
        self.config = config or {}
        self.tz = pytz.timezone(tz_name)

        # Configurable fields (safe defaults)
        self.start_time = self.config.get("start_time", "04:00")
        self.end_time = self.config.get("end_time", "16:00")
        self.data_delay_minutes = int(self.config.get("data_delay_minutes", 0))

        # Parse into time objects
        self._start = self._parse_hhmm(self.start_time)
        self._end = self._parse_hhmm(self.end_time)

    def _parse_hhmm(self, hhmm: str) -> time:
        hh, mm = hhmm.split(":")
        return time(int(hh), int(mm))

    def _cadence_for_dt(self, dt: datetime) -> int:
        # Mirror scanner cadence rules (Phase 1 spec)
        h = dt.hour
        m = dt.minute
        if h < 9:
            return 30
        if h == 9 and m < 15:
            return 15
        return 5

    def _iter_planned_wakes(self, max_lines: int | None = None) -> List[datetime]:
        """Generate planned wake datetimes for today (tz-aware)."""
        today = datetime.now(self.tz).date()
        current = datetime.combine(today, self._start).replace(tzinfo=self.tz)
        end_dt = datetime.combine(today, self._end).replace(tzinfo=self.tz)

        out: List[datetime] = []
        while current <= end_dt:
            out.append(current)
            cadence = self._cadence_for_dt(current)
            current = current + timedelta(minutes=cadence)
            if max_lines and len(out) >= max_lines:
                break
        return out

    def dry_run(self, output_path: str | Path | None = None, max_lines: int = 500) -> List[str]:
        """Run a dry-run that writes planned wake times to stdout and a log file.

        Returns the list of lines written.
        """
        planned = self._iter_planned_wakes(max_lines=max_lines)
        lines: List[str] = []

        for dt in planned:
            cadence = self._cadence_for_dt(dt)
            ts = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            line = f"{ts} | cadence_minutes={cadence} | data_delay_minutes={self.data_delay_minutes}"
            lines.append(line)

        # Write to disk if requested
        if output_path:
            outp = Path(output_path)
            outp.parent.mkdir(parents=True, exist_ok=True)
            try:
                with outp.open("w") as fh:
                    for l in lines:
                        fh.write(l + "\n")
                LOG.info(f"Scheduler dry_run: wrote {len(lines)} lines to {outp}")
            except Exception:
                LOG.exception("Failed to write scheduler dry-run output")

        # Also print to stdout for immediate visibility
        for l in lines:
            print(l)

        return lines


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scheduler dry-run (Phase 1)")
    parser.add_argument("--out", "-o", default="logs/scheduler_dryrun.log", help="Path to write dry-run log")
    parser.add_argument("--max", "-m", type=int, default=200, help="Max lines to emit")
    args = parser.parse_args()

    # Configure minimal logging
    logging.basicConfig(level=logging.INFO)

    sched = Scheduler()
    sched.dry_run(output_path=Path(args.out), max_lines=args.max)
