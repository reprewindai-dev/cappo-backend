"""VRE-0 gate: re-verify the committed run and the tamper suite.

Needs no root and runs no workloads: it checks the stored artifacts of
vre0/gate/runs/20261011T035711Z against keys pinned here, outside the run.
"""

import copy
import json
import pathlib

import pytest

from vre0.gate import harness, tamper
from vre0.gate.verify import load_run, outcome_from_evidence, verify_run

RUN = pathlib.Path(__file__).resolve().parents[1] / "vre0/gate/runs/20261011T035711Z"
PINNED = {
    "issuer": {"ce149b8514366095": "K/KXfMqw5j4QBbcseV2mk3g1i8WE0BWPiDye1gKtRr8="},
    "executor": {"b7d2d0a1e520f863": "RDvvYY5fGpirStFXvvUhdsHUEa1wTZci5siAQxir8hs="},
}


@pytest.fixture(scope="module")
def run():
    return load_run(RUN)


def test_committed_run_verifies_under_pinned_keys(run):
    report = verify_run(copy.deepcopy(run), PINNED)
    assert report["failures"] == []
    assert report["pairs"] == 220
    for scenario, expected in run["manifest"]["expected"].items():
        assert report["stats"][scenario]["outcomes"] == {expected: 20}


def test_manifest_keys_are_the_pinned_keys(run):
    assert run["manifest"]["trusted_keys"] == PINNED


def test_harness_and_verifier_rules_agree_on_every_record(run):
    for name, rec in run["records"].items():
        ev = rec["evidence"]
        assert harness.classify(ev) == outcome_from_evidence(ev) == rec["receipt"]["payload"]["outcome"], name


def test_guest_claims_never_decide_the_outcome(run):
    for p in run["manifest"]["pairs"]:
        if p["scenario"] == "lying_bomb":
            ev = run["records"][p["attacker"]]["evidence"]
            assert ev["untrusted_guest_output"] == "COMPLETED ok"
            assert outcome_from_evidence(ev) == "ENVELOPE_MEMORY_EXCEEDED"


def test_unknown_key_is_not_trusted(run):
    bogus = {"issuer": PINNED["issuer"],
             "executor": {"0000000000000000": PINNED["executor"]["b7d2d0a1e520f863"]}}
    report = verify_run(copy.deepcopy(run), bogus)
    assert not report["valid"]


@pytest.mark.parametrize("case", [c for c in tamper.cases(*(
    tamper.Ed25519PrivateKey.generate() for _ in range(2))) if c.expect], ids=lambda c: c.name)
def test_tampered_runs_are_rejected(run, case):
    evil_exec = tamper.Ed25519PrivateKey.generate()
    evil_issuer = tamper.Ed25519PrivateKey.generate()
    # Rebuild the case bound to keys this test controls.
    case = next(c for c in tamper.cases(evil_exec, evil_issuer) if c.name == case.name)
    pub = tamper.contract.public_key_b64(evil_exec)
    trust = PINNED if case.attacker == "outsider" else {
        "issuer": PINNED["issuer"],
        "executor": {**PINNED["executor"], tamper.contract.key_id(pub): pub},
    }
    mutated = copy.deepcopy(run)
    case.mutate(mutated)
    report = verify_run(mutated, trust)
    assert not report["valid"]
    assert any(case.expect in f for f in report["failures"]), json.dumps(report["failures"][:3])
