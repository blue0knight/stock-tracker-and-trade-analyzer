from src.core.diagnostics import Diagnostics


def test_counters_and_timeit():
    d = Diagnostics(enabled=True)
    d.record_event("x")
    d.record_event("x", 2)
    assert d.counters["x"] == 3

    @d.timeit("t")
    def fast():
        return 1

    assert fast() == 1
    assert "t" in d.timers
    out = d.export_metrics()
    assert "counters" in out and "timers" in out
