import sys
import os
import pytest

# Ensure project root is importable as top-level 'src' package during tests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set minimal environment for tests and isolate external config.

    - Ensure POLYGON_API_KEY exists so modules don't raise at import
    - Other env isolation can be added here
    """
    monkeypatch.setenv('POLYGON_API_KEY', 'testkey')
    yield
