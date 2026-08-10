from pathlib import Path


def test_pipeline_doc_uses_canonical_cappo_port_and_unverified_runtime() -> None:
    text = Path("agents/PIPELINE.md").read_text(encoding="utf-8")

    assert "8002" in text
    assert "NOT_VERIFIED" in text
    assert ":8000" not in text
    assert "PORT=8000" not in text
    assert "CAPPO `3000`" not in text
    assert "UPTIME: 99.97%" not in text
    assert "● Healthy" not in text
