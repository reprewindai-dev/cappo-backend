"""Compilation moved to the ABIDE service (wiring W-06); CAPPO keeps only authority."""

from fastapi.testclient import TestClient


def test_compile_routes_point_to_abide(client: TestClient):
    for path, location in (
        ("/api/v1/gpc/compile", "/api/abide/v1/blueprint/compile"),
        ("/api/v1/gpc/pipeline/compile", "/api/abide/v1/pipeline/compile"),
    ):
        response = client.post(path, json={"intent": "read the counter"})
        assert response.status_code == 410
        assert response.json() == {
            "error": "MOVED_TO_ABIDE",
            "detail": "Plan/contract compilation is owned by ABIDE (W-06).",
            "location": location,
        }
