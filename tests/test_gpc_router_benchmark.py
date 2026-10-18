import pytest

try:
    import pytest_benchmark  # noqa: F401
except ImportError:
    pytestmark = pytest.mark.skip(reason="pytest-benchmark not installed")

from fastapi.testclient import TestClient


def test_get_stats_benchmark(client: TestClient, benchmark=None):
    if benchmark is None:
        pytest.skip("benchmark fixture not available")
    def fetch_stats():
        response = client.get("/api/v1/gpc/stats")
        assert response.status_code == 200

    benchmark(fetch_stats)
