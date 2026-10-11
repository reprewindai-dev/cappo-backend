"""VDB-C1.0: admission, preconditions, crash matrix, duplicates, offline verification.

Crashes are real process deaths (os._exit inside a subprocess at the named
point), not exceptions. Recovery runs in a fresh process or object.
"""

import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid

import pytest

from vdb_c1 import canon, coordinator, verify
from vdb_c1.coordinator import CRASH_EXIT, Coordinator, init_home, make_intent

REPO = pathlib.Path(__file__).resolve().parents[1]
NS = "ns:veklom:c1:test"


def run_cli(*args, timeout=60):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "vdb_c1.cli", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture()
def home(tmp_path):
    sk, kid, pub = canon.generate_key()
    h = tmp_path / "home"
    init_home(h, NS, {kid: pub})
    return {"path": h, "sk": sk, "kid": kid}


def intent_for(home, key="k1", value="v1", **kw):
    c = Coordinator.open(home["path"])
    st = c.state()
    args = dict(expected_generation=st["generation"], expected_root=st["root"])
    args.update(kw)
    return make_intent(home["sk"], home["kid"], NS, key, value, **args)


def write(tmp, obj, name):
    p = tmp / name
    p.write_text(json.dumps(obj))
    return p


def kinds_for(home, intent_id):
    c = Coordinator.open(home["path"])
    return [r["kind"] for r in c.journal.records if r["intent_id"] == intent_id]


def clean_room_verify(tmp_path, bundle, trust_store_path):
    """Separate process, empty directory, only bundle + public trust store."""
    room = tmp_path / f"room-{uuid.uuid4().hex[:8]}"
    room.mkdir()
    shutil.copy(REPO / "vdb_c1" / "verify.py", room / "verify.py")
    (room / "bundle.json").write_text(json.dumps(bundle))
    shutil.copy(trust_store_path, room / "trust_store.json")
    out = subprocess.run([sys.executable, "-I", "verify.py", "bundle.json", "trust_store.json"],
                         cwd=room, capture_output=True, text=True, timeout=60,
                         env={"PATH": os.environ.get("PATH", "")})
    return out.returncode, json.loads(out.stdout)


# ---------------------------------------------------------------- happy path

def test_finalized_consequence_verifies_in_a_clean_process(home, tmp_path):
    c = Coordinator.open(home["path"])
    before = c.state()
    res = c.submit(intent_for(home))
    assert res["state"] == "FINALIZED"
    after = c.state()
    assert after["generation"] == before["generation"] + 1
    assert dict(after["entries"]) == {"k1": "v1"}
    assert c.adapter.execute_calls == 1 and c.adapter.commit_calls == 1
    bundle = c.export_bundle(res["intent_id"])
    code, verdict = clean_room_verify(tmp_path, bundle, home["path"] / "trust_store.json")
    assert (code, verdict["verdict"]) == (0, "VALID_FINALIZED")


def test_second_intent_chains_on_the_first(home, tmp_path):
    c = Coordinator.open(home["path"])
    r1 = c.submit(intent_for(home, "k1", "v1"))
    r2 = c.submit(intent_for(home, "k2", "v2"))
    r3 = c.submit(intent_for(home, "k1", "v1b"))
    assert [r["state"] for r in (r1, r2, r3)] == ["FINALIZED"] * 3
    assert dict(c.state()["entries"]) == {"k1": "v1b", "k2": "v2"}
    for r in (r1, r2, r3):
        code, v = clean_room_verify(tmp_path, c.export_bundle(r["intent_id"]),
                                    home["path"] / "trust_store.json")
        assert v["verdict"] == "VALID_FINALIZED", v


# ------------------------------------------------- admission and preconditions

def _assert_no_consequence(c, before):
    assert c.adapter.execute_calls == 0 and c.adapter.commit_calls == 0
    assert c.state() == before


@pytest.mark.parametrize("case", [
    "bad_signature", "untrusted_key", "expired", "not_yet_valid", "wrong_namespace",
    "wrong_op", "extra_member", "float_value", "kid_mismatch", "ttl_too_long", "not_an_object",
])
def test_invalid_admission_runs_nothing(home, case):
    signed = intent_for(home)
    p = signed["payload"]
    now = canon.parse_time(p["issued_at"])
    if case == "bad_signature":
        p["value"] = "tampered"
    elif case == "untrusted_key":
        sk, kid, _ = canon.generate_key()
        signed = make_intent(sk, kid, NS, "k1", "v1", expected_generation=0,
                             expected_root=p["expected_root"])
    elif case == "expired":
        signed = make_intent(home["sk"], home["kid"], NS, "k1", "v1", expected_generation=0,
                             expected_root=p["expected_root"], now=now - 400, ttl_s=300)
    elif case == "not_yet_valid":
        signed = make_intent(home["sk"], home["kid"], NS, "k1", "v1", expected_generation=0,
                             expected_root=p["expected_root"], now=now + 120, ttl_s=300)
    elif case == "wrong_namespace":
        signed = make_intent(home["sk"], home["kid"], "ns:other", "k1", "v1",
                             expected_generation=0, expected_root=p["expected_root"])
    elif case == "wrong_op":
        p["op"] = "DELETE"
        signed = canon.sign(p, home["sk"], home["kid"])
    elif case == "extra_member":
        p["sql"] = "DROP TABLE kv"
        signed = canon.sign(p, home["sk"], home["kid"])
    elif case == "float_value":
        p["expected_generation"] = 0.0
        signed = {**signed, "payload": p}
    elif case == "kid_mismatch":
        signed["kid"] = "ed25519:0000000000000000"
    elif case == "ttl_too_long":
        signed = make_intent(home["sk"], home["kid"], NS, "k1", "v1", expected_generation=0,
                             expected_root=p["expected_root"], ttl_s=3600)
    elif case == "not_an_object":
        signed = {"payload": "UPSERT k1 v1", "sig": "x", "kid": home["kid"], "alg": "Ed25519"}
    c = Coordinator.open(home["path"])
    before = c.state()
    res = c.submit(signed)
    assert res["state"] == "ADMISSION_REJECTED", res
    _assert_no_consequence(c, before)
    assert c.journal.records[-1]["kind"] == "ADMISSION_REJECTED"


def test_stale_generation_is_rejected_before_any_work(home):
    c = Coordinator.open(home["path"])
    stale = intent_for(home, "k2", "x")             # bound to generation 0
    assert c.submit(intent_for(home))["state"] == "FINALIZED"
    c2 = Coordinator.open(home["path"])
    before = c2.state()
    res = c2.submit(stale)
    assert (res["state"], res["reason"]) == ("PRECONDITION_REJECTED", "E_STALE_GENERATION")
    _assert_no_consequence(c2, before)


def test_root_mismatch_is_rejected(home):
    c = Coordinator.open(home["path"])
    res = c.submit(intent_for(home, expected_root="sha256:" + "0" * 64))
    assert (res["state"], res["reason"]) == ("PRECONDITION_REJECTED", "E_ROOT_MISMATCH")
    _assert_no_consequence(c, Coordinator.open(home["path"]).state())


def test_same_intent_id_with_different_bytes_is_a_conflict(home):
    c = Coordinator.open(home["path"])
    first = intent_for(home)
    assert c.submit(first)["state"] == "FINALIZED"
    p = dict(first["payload"], value="other", expected_generation=1,
             expected_root=c.state()["root"])
    c2 = Coordinator.open(home["path"])
    before = c2.state()
    res = c2.submit(canon.sign(p, home["sk"], home["kid"]))
    assert (res["state"], res["reason"]) == ("ADMISSION_REJECTED", "E_INTENT_CONFLICT")
    _assert_no_consequence(c2, before)


def test_exact_retry_returns_the_original_certificate(home):
    signed = intent_for(home)
    r1 = Coordinator.open(home["path"]).submit(signed)
    c = Coordinator.open(home["path"])
    r2 = c.submit(signed)
    assert r2["replayed"] is True and r2["certificate"] == r1["certificate"]
    assert c.adapter.execute_calls == 0 and c.state()["generation"] == 1


# -------------------------------------------------------------- crash matrix

def crash(home, tmp_path, point):
    signed = intent_for(home)
    f = write(tmp_path, signed, f"intent-{point}.json")
    out = run_cli("submit", "--home", str(home["path"]), "--intent", str(f), "--crash-at", point)
    assert out.returncode == CRASH_EXIT, (out.stdout, out.stderr)
    assert out.stdout == ""                      # the caller never heard back
    return signed


def test_crash_a_after_prepared(home, tmp_path):
    signed = crash(home, tmp_path, "after_prepared")
    iid = signed["payload"]["intent_id"]
    c = Coordinator.open(home["path"])
    assert c.state()["generation"] == 0 and c.adapter.applied(iid) is None
    assert kinds_for(home, iid) == ["PREPARED"]
    c.recover()
    assert kinds_for(home, iid) == ["PREPARED", "ABORTED"]
    retry = Coordinator.open(home["path"])
    res = retry.submit(signed)
    assert res["state"] == "ABORTED" and "certificate" not in res
    assert retry.adapter.execute_calls == 0 and retry.state()["generation"] == 0


def test_crash_b_inside_transaction_rolls_back(home, tmp_path):
    signed = crash(home, tmp_path, "before_storage_commit")
    iid = signed["payload"]["intent_id"]
    c = Coordinator.open(home["path"])
    assert c.state()["generation"] == 0 and c.state()["entries"] == []
    assert c.adapter.applied(iid) is None
    assert kinds_for(home, iid) == ["PREPARED", "COMMITTING"]
    c.recover()
    assert kinds_for(home, iid) == ["PREPARED", "COMMITTING", "COMMIT_UNCERTAIN", "ABORTED"]
    assert c.state()["generation"] == 0 and c.adapter.execute_calls == 0


def test_crash_c_after_storage_commit_recovers_same_effect(home, tmp_path):
    signed = crash(home, tmp_path, "after_storage_commit")
    iid = signed["payload"]["intent_id"]
    c = Coordinator.open(home["path"])
    assert c.state()["generation"] == 1 and c.adapter.applied(iid) is not None
    assert kinds_for(home, iid) == ["PREPARED", "COMMITTING"]
    res = c.submit(signed)                       # the caller retries
    assert res["state"] == "FINALIZED"
    assert c.adapter.execute_calls == 0          # recovered, never re-executed
    assert c.state()["generation"] == 1 and dict(c.state()["entries"]) == {"k1": "v1"}
    assert kinds_for(home, iid) == ["PREPARED", "COMMITTING", "COMMIT_UNCERTAIN",
                                    "COMMITTED", "FINALIZED"]
    committed = [r for r in c.journal.records if r["intent_id"] == iid and r["kind"] == "COMMITTED"]
    assert committed[0]["body"]["recovered"] is True
    code, v = clean_room_verify(tmp_path, c.export_bundle(iid), home["path"] / "trust_store.json")
    assert v["verdict"] == "VALID_FINALIZED", v


def test_crash_c2_after_committed_record(home, tmp_path):
    signed = crash(home, tmp_path, "after_committed_record")
    iid = signed["payload"]["intent_id"]
    assert kinds_for(home, iid) == ["PREPARED", "COMMITTING", "COMMITTED"]
    c = Coordinator.open(home["path"])
    res = c.submit(signed)
    assert res["state"] == "FINALIZED" and c.adapter.execute_calls == 0
    code, v = clean_room_verify(tmp_path, c.export_bundle(iid), home["path"] / "trust_store.json")
    assert v["verdict"] == "VALID_FINALIZED", v


def test_crash_d_response_lost_retry_returns_original_receipt(home, tmp_path):
    signed = crash(home, tmp_path, "after_finalized")
    iid = signed["payload"]["intent_id"]
    c = Coordinator.open(home["path"])
    stored = [r for r in c.journal.records if r["intent_id"] == iid and r["kind"] == "FINALIZED"]
    assert len(stored) == 1
    res = c.submit(signed)
    assert res["replayed"] is True
    assert res["certificate"] == stored[0]["body"]["certificate"]
    assert c.adapter.execute_calls == 0 and c.state()["generation"] == 1
    assert [k for k in kinds_for(home, iid) if k == "FINALIZED"] == ["FINALIZED"]


def test_duplicate_storm_applies_once(home, tmp_path):
    signed = intent_for(home)
    f = write(tmp_path, signed, "storm.json")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    procs = [subprocess.Popen([sys.executable, "-m", "vdb_c1.cli", "submit", "--home",
                               str(home["path"]), "--intent", str(f)], cwd=REPO, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(12)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        results.append(json.loads(out))
    assert {r["state"] for r in results} == {"FINALIZED"}
    assert len({json.dumps(r["certificate"], sort_keys=True) for r in results}) == 1
    assert sum(1 for r in results if not r["replayed"]) == 1
    c = Coordinator.open(home["path"])
    assert c.state()["generation"] == 1 and c.adapter.applied_count() == 1
    assert kinds_for(home, signed["payload"]["intent_id"]).count("FINALIZED") == 1


# ------------------------------------------------------------- journal safety

def test_torn_final_line_is_dropped_and_mid_file_damage_stops(home):
    Coordinator.open(home["path"]).submit(intent_for(home))
    j = home["path"] / "journal.jsonl"
    data = j.read_bytes()
    j.write_bytes(data + b'{"protocol":"veklom.vdb.c1.jour')
    c = Coordinator.open(home["path"])
    assert c.journal.torn_tail_bytes > 0 and len(c.journal.records) == 5
    assert j.read_bytes() == data
    lines = data.split(b"\n")
    lines[1] = lines[1].replace(b'"PREPARED"', b'"ABORTED"', 1)
    j.write_bytes(b"\n".join(lines))
    with pytest.raises(coordinator.JournalCorrupt):
        Coordinator.open(home["path"])


# ------------------------------------------------------- verifier rejections

@pytest.fixture()
def finalized(home):
    c = Coordinator.open(home["path"])
    r = c.submit(intent_for(home))
    trust = json.loads((home["path"] / "trust_store.json").read_text())
    return c.export_bundle(r["intent_id"]), trust, home


def rechain(journal):
    prev = "genesis"
    for i, rec in enumerate(journal):
        rec["seq"], rec["prev"] = i, prev
        rec.pop("hash", None)
        rec["hash"] = canon.digest(rec)
        prev = rec["hash"]


def rec(bundle, kind):
    return next(r for r in bundle["journal"] if r["kind"] == kind)


def tamper_cases():
    def edit_no_rehash(b, t, h):
        rec(b, "COMMITTED")["body"]["post_entries"][0][1] = "evil"

    def intent_value_rehashed(b, t, h):
        rec(b, "PREPARED")["body"]["signed_intent"]["payload"]["value"] = "evil"
        rechain(b["journal"])

    def post_value_substituted(b, t, h):
        body = rec(b, "COMMITTED")["body"]
        body["post_entries"] = [["k1", "evil"]]
        body["post_root"] = canon.state_root(NS, body["post_generation"], body["post_entries"])
        rechain(b["journal"])

    def pre_entries_forged(b, t, h):
        rec(b, "PREPARED")["body"]["pre_entries"] = [["ghost", "x"]]
        rechain(b["journal"])

    def generation_skipped(b, t, h):
        body = rec(b, "COMMITTED")["body"]
        body["post_generation"] += 1
        body["post_root"] = canon.state_root(NS, body["post_generation"], body["post_entries"])
        rechain(b["journal"])

    def duplicate_finality(b, t, h):
        b["journal"].append(copy.deepcopy(rec(b, "FINALIZED")))
        rechain(b["journal"])

    def cert_field_edited(b, t, h):
        b["receipt"]["certificate"]["payload"]["post_root"] = "sha256:" + "1" * 64

    def cert_by_unpinned_key(b, t, h):
        sk, kid, _ = canon.generate_key()
        b["receipt"]["certificate"] = canon.sign(b["receipt"]["certificate"]["payload"], sk, kid)

    def authority_not_pinned(b, t, h):
        t["authority_keys"] = {}

    def first_record_dropped(b, t, h):
        b["journal"].pop(0)

    def receipt_digest_wrong(b, t, h):
        b["receipt"]["certificate_digest"] = "sha256:" + "2" * 64

    def journal_missing(b, t, h):
        del b["journal"]

    return [
        ("edit_no_rehash", edit_no_rehash, "REJECTED_JOURNAL_INCONSISTENT"),
        ("intent_value_rehashed", intent_value_rehashed, "REJECTED_BAD_SIGNATURE"),
        ("post_value_substituted", post_value_substituted, "REJECTED_PAYLOAD_SUBSTITUTION"),
        ("pre_entries_forged", pre_entries_forged, "REJECTED_ROOT_MISMATCH"),
        ("generation_skipped", generation_skipped, "REJECTED_GENERATION_MISMATCH"),
        ("duplicate_finality", duplicate_finality, "REJECTED_DUPLICATE_FINALITY"),
        ("cert_field_edited", cert_field_edited, "REJECTED_BAD_SIGNATURE"),
        ("cert_by_unpinned_key", cert_by_unpinned_key, "REJECTED_UNTRUSTED_KEY"),
        ("authority_not_pinned", authority_not_pinned, "REJECTED_UNTRUSTED_KEY"),
        ("first_record_dropped", first_record_dropped, "REJECTED_JOURNAL_INCONSISTENT"),
        ("receipt_digest_wrong", receipt_digest_wrong, "REJECTED_RECEIPT_MISMATCH"),
        ("journal_missing", journal_missing, "REJECTED_INCOMPLETE_BUNDLE"),
    ]


def test_untampered_bundle_is_valid(finalized):
    bundle, trust, _ = finalized
    assert verify.verify_bundle(bundle, trust)["verdict"] == "VALID_FINALIZED"


@pytest.mark.parametrize("name, mutate, expected", tamper_cases(), ids=[c[0] for c in tamper_cases()])
def test_tampered_bundle_is_rejected(finalized, name, mutate, expected):
    bundle, trust, home = finalized
    b, t = copy.deepcopy(bundle), copy.deepcopy(trust)
    mutate(b, t, home)
    assert verify.verify_bundle(b, t)["verdict"] == expected


def test_verifier_imports_nothing_from_the_package():
    src = (REPO / "vdb_c1" / "verify.py").read_text()
    assert "vdb_c1" not in src.replace('"vdb_c1"', "")
    assert "import coordinator" not in src and "from ." not in src
