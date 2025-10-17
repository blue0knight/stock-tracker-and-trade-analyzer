import os
import pytest

if os.getenv("CI"):
    pytest.skip("Skipping live Polygon API tests in CI environment", allow_module_level=True)


from src.adapters.polygon_client import PolygonClient

pc = PolygonClient()

# Snapshot test
snap = pc.get_snapshot("AAPL")
print("Snapshot:", snap)

# Aggregates test (last Friday)
aggs = pc.get_aggregates("AAPL", "2025-09-19", "2025-09-19")
print("Agg count:", aggs["count"])
print("First bar:", aggs["results"][0] if aggs["results"] else "None")
