from pathlib import Path


def test_main_runtime_contains_no_synthetic_vnp_prober() -> None:
    text = Path("cappo_backend/main.py").read_text(encoding="utf-8")

    assert "vnp_prober_loop" not in text
    assert "ENABLE_VNP_PROBER" not in text
    assert "CAPPO_ENABLE_INTERNAL_VNP_PROBER" not in text
    assert "random.randint" not in text
    assert "random.random" not in text
