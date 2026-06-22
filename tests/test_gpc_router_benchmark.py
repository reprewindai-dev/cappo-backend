from fastapi.testclient import TestClient


def test_get_stats_benchmark(client: TestClient, benchmark):
    def fetch_stats():
        response = client.get("/api/v1/gpc/stats")
        assert response.status_code == 200

    benchmark(fetch_stats)
