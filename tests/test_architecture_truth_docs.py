import re
from pathlib import Path


def test_pipeline_doc_uses_canonical_cappo_port_and_unverified_runtime() -> None:
    text = Path("agents/PIPELINE.md").read_text(encoding="utf-8")

    assert "| Application listener | `8002` |" in text
    assert "| Container / service target | `8002` |" in text
    assert "  cappo_listener: NOT_VERIFIED" in text
    assert "configured listener: 8002" in text
    assert "observed listener: NOT_VERIFIED" in text

    assert not re.search(
        r"(?im)^\s*(?:listener|target|port|PORT)\s*[:=]\s*`?(?:3000|8000)\b",
        text,
    )
    assert not re.search(
        r"(?im)^\s*(?:uptime|latency|error rate|health|status|release state)"
        r"\s*[:=]\s*"
        r"(?!NOT_VERIFIED\b|UNAVAILABLE\b|PENDING_MEASUREMENT\b|EXAMPLE_ONLY\b).+",
        text,
    )
