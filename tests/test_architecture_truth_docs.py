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
    runtime_claims = re.finditer(
        r"(?im)^\s*(?:uptime|latency|error rate|health|status|release state)"
        r"\s*[:=]\s*(?P<value>.+)$",
        text,
    )
    allowed_labels = (
        "NOT_VERIFIED",
        "UNAVAILABLE",
        "PENDING_MEASUREMENT",
        "EXAMPLE_ONLY",
    )
    for claim in runtime_claims:
        assert claim.group("value").strip().startswith(allowed_labels), claim.group(0)
