"""VDB-C1 EEE v0.1.0 output, verified by CAPPO's own EEEVerifier.

The envelopes are built by vdb_c1/eee.py, written independently of
cappo_backend/services/eee.py. Agreement between the two is the point.
"""

import copy
import json
import os
import pathlib
import subprocess
import sys

import pytest

from cappo_backend.services.eee import EEEVerifier, VerificationVerdict
from vdb_c1 import canon
from vdb_c1.coordinator import Coordinator, init_home, make_intent

REPO = pathlib.Path(__file__).resolve().parents[1]
NS = "ns:veklom:c1:eee"
UNRESOLVED = VerificationVerdict.VALID_WITH_UNRESOLVED_REFS


@pytest.fixture()
def home(tmp_path):
    sk, kid, pub = canon.generate_key()
    h = tmp_path / "home"
    init_home(h, NS, {kid: pub})
    return {"path": h, "sk": sk, "kid": kid}


def intent(home, key="k1", value="v1", **kw):
    st = Coordinator.open(home["path"]).state()
    args = {"expected_generation": st["generation"], "expected_root": st["root"], **kw}
    return make_intent(home["sk"], home["kid"], NS, key, value, **args)


def cappo_verifier(home):
    trust = json.loads((home["path"] / "trust_store.json").read_text())
    return EEEVerifier({kid: canon.unb64u(pub) for kid, pub in trust["eee_issuer_keys"].items()})


def envelopes(home):
    return Coordinator.open(home["path"]).eee_envelopes()


def test_finalized_upsert_is_a_completed_envelope_cappo_accepts(home):
    Coordinator.open(home["path"]).submit(intent(home))
    [env] = envelopes(home)
    report = cappo_verifier(home).verify(env)
    # C1 has no revocation list; EEE requires saying so as a downgrade signal.
    assert (report.verdict, report.reasons) == (UNRESOLVED, ["REVOCATION_NOT_CHECKED"])
    assert env["status"] == "completed" and env["actual_effects"] == [f"state:upsert:{NS}"]
    assert env["output_commitment"] == Coordinator.open(home["path"]).state()["root"]
    assert env["enforcement_mode"] == "fail-closed"


@pytest.mark.parametrize("make", ["bad_signature", "wrong_namespace", "stale_generation", "root_mismatch"])
def test_denials_are_signed_evidence_cappo_accepts(home, make):
    c = Coordinator.open(home["path"])
    if make == "bad_signature":
        s = intent(home)
        s["payload"]["value"] = "x"
    elif make == "wrong_namespace":
        st = c.state()
        s = make_intent(home["sk"], home["kid"], "ns:other", "k", "v",
                        expected_generation=st["generation"], expected_root=st["root"])
    elif make == "stale_generation":
        s = intent(home, expected_generation=5)
    else:
        s = intent(home, expected_root="sha256:" + "0" * 64)
    res = c.submit(s)
    [env] = envelopes(home)
    report = cappo_verifier(home).verify(env)
    assert (report.verdict, report.reasons) == (UNRESOLVED, ["REVOCATION_NOT_CHECKED"]), res
    assert env["status"] == "denied" and env["actual_effects"] == []
    assert env["policy_decisions"][-1]["decision"] == "deny"
    assert env["policy_decisions"][-1]["reason_code"] == res["reason"]
    if res["state"] == "PRECONDITION_REJECTED":            # admitted: the real window
        assert env["authority_window"] == {"not_before": s["payload"]["issued_at"],
                                           "not_after": s["payload"]["expires_at"]}
    else:                                                  # no authority established
        assert env["authority_window"]["not_before"] == env["authority_window"]["not_after"]


def test_aborted_crash_is_an_error_envelope(home, tmp_path):
    f = tmp_path / "i.json"
    f.write_text(json.dumps(intent(home)))
    env_vars = {**os.environ, "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-m", "vdb_c1.cli", "submit", "--home", str(home["path"]),
                          "--intent", str(f), "--crash-at", "after_prepared"], cwd=REPO,
                         env=env_vars, capture_output=True, text=True, timeout=60)
    assert out.returncode == 77
    Coordinator.open(home["path"]).recover()
    [env] = envelopes(home)
    assert env["status"] == "error" and env["actual_effects"] == []
    assert cappo_verifier(home).verify(env).verdict == UNRESOLVED


def test_one_envelope_per_terminal_attempt_and_none_for_uncertain(home):
    c = Coordinator.open(home["path"])
    c.submit(intent(home, "a", "1"))
    c.submit(intent(home, "b", "2"))
    s = intent(home)
    s["payload"]["value"] = "forged"
    c.submit(s)
    envs = envelopes(home)
    assert [e["status"] for e in envs] == ["completed", "completed", "denied"]
    assert len({e["execution_id"] for e in envs}) == 3
    assert all(cappo_verifier(home).verify(e).verdict == UNRESOLVED for e in envs)


def test_envelopes_are_deterministic_from_the_journal(home):
    Coordinator.open(home["path"]).submit(intent(home))
    assert envelopes(home) == envelopes(home)


@pytest.mark.parametrize("edit, reason", [
    (lambda e: e.update(status="denied"), "ENVELOPE_HASH_MISMATCH"),
    (lambda e: e["actual_effects"].append("network:evil.example:443"), "ENVELOPE_HASH_MISMATCH"),
    (lambda e: e["budget"]["consumed"].update(writes=0), "ENVELOPE_HASH_MISMATCH"),
    (lambda e: e["signatures"][0].update(value=e["signatures"][0]["value"][::-1]), "ISSUER_SIGNATURE_INVALID"),
])
def test_tampered_envelope_is_rejected_by_cappo(home, edit, reason):
    Coordinator.open(home["path"]).submit(intent(home))
    env = copy.deepcopy(envelopes(home)[0])
    edit(env)
    report = cappo_verifier(home).verify(env)
    assert (report.verdict, report.reasons) == (VerificationVerdict.INVALID, [reason])


def test_unpinned_issuer_key_is_rejected(home):
    Coordinator.open(home["path"]).submit(intent(home))
    env = envelopes(home)[0]
    assert EEEVerifier({}).verify(env).verdict == VerificationVerdict.INVALID


def test_eee_issuer_key_is_separate_from_the_finalizer_key(home):
    Coordinator.open(home["path"]).submit(intent(home))
    envelopes(home)
    trust = json.loads((home["path"] / "trust_store.json").read_text())
    assert set(trust["eee_issuer_keys"]).isdisjoint(trust["finalizer_keys"])
    assert oct((home["path"] / "eee_issuer.key").stat().st_mode & 0o777) == "0o600"
