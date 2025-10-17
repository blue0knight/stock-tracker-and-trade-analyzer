def test_scheduler_router_system2_dryrun(monkeypatch):
    # Ensure diagnostics enabled for the run and run the dry-run harness
    from src.core.diagnostics import DIAG
    import runpy

    DIAG.enabled = True
    runpy.run_path('scripts/dry_run_integration.py', run_name='__main__')

    metrics = DIAG.export_metrics()
    # Expect at least an invocation counter for system2
    assert 'system2.invoked' in metrics.get('counters', {})
