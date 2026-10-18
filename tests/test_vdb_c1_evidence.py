"""Re-verify the committed VDB-C1.0 proof run against keys pinned here."""

import json
import pathlib

import pytest

from vdb_c1.verify import verify_bundle

RUN = pathlib.Path(__file__).resolve().parents[1] / "vdb_c1/evidence/20261011T044036Z"
PINNED = {"authority_keys": {"ed25519:fb5b1f7f2a5ff6ec": "tTHzdMWI0N-JCr2SJ_9ZEe0QXpiNU5ByNPYg49MCsxU"}, "finalizer_keys": {"ed25519:e6b72fdb423ace14": "WDplIMEUm8axPTfE0RrpPlZ26IRwIFZ6hAJadaKLRt4"}}


def test_committed_trust_store_is_the_pinned_one():
    trust = json.loads((RUN / "trust_store.json").read_text())
    assert {k: v for k, v in trust.items() if k != "protocol"} == PINNED


@pytest.mark.parametrize("name", ["normal", "crash_c_recovered"])
def test_committed_bundle_verifies(name):
    bundle = json.loads((RUN / f"{name}.bundle.json").read_text())
    assert verify_bundle(bundle, PINNED)["verdict"] == "VALID_FINALIZED"


def test_recorded_crash_run_recovered_without_reexecution():
    run = json.loads((RUN / "manifest.json").read_text())["runs"]["crash_c_recovered"]
    assert (run["first_submit_exit"], run["first_submit_stdout"]) == (77, "")
    assert (run["after_crash_generation"], run["after_crash_applied"]) == (2, 1)
    assert run["retry_result"] == "FINALIZED"
    bundle = json.loads((RUN / "crash_c_recovered.bundle.json").read_text())
    kinds = [r["kind"] for r in bundle["journal"] if r["intent_id"] == run["intent_id"]]
    assert kinds == ["PREPARED", "COMMITTING", "COMMIT_UNCERTAIN", "COMMITTED", "FINALIZED"]
