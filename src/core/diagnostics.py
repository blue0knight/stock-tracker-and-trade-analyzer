from __future__ import annotations

import time
from functools import wraps
from typing import Callable, Dict, Any


class Diagnostics:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.counters: Dict[str, int] = {}
        self.timers: Dict[str, list[float]] = {}

    def record_event(self, name: str, value: int = 1) -> None:
        if not self.enabled:
            return
        self.counters[name] = self.counters.get(name, 0) + value

    def timeit(self, name: str) -> Callable:
        def decorator(fn: Callable):
            @wraps(fn)
            def wrapper(*a, **kw):
                if not self.enabled:
                    return fn(*a, **kw)
                t0 = time.perf_counter()
                try:
                    return fn(*a, **kw)
                finally:
                    elapsed = time.perf_counter() - t0
                    self.timers.setdefault(name, []).append(elapsed)
            return wrapper
        return decorator

    def export_metrics(self) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        return {"counters": dict(self.counters), "timers": {k: list(v) for k, v in self.timers.items()}}


# single global instance (disabled by default; enable via config)
DIAG = Diagnostics(enabled=False)
