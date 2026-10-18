"""VDB-C1 W1: a witness countersigns history only forward; forks are provable."""

import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from vdb_c1 import canon
from vdb_c1.coordinator import Coordinator, init_home, make_intent
from vdb_c1.verify import verify_bundle
from vdb_c1.witness import Witness, WitnessRefused

REPO = pathlib.Path(__file__).resolve().parents[1]
NS = "ns:veklom:c1:witness"


@pytest.fixture()
def world(tmp_path):
    sk, kid, pub = canon.generate_key()
    home = tmp_path / "home"
    trust = init_home(home, NS, {kid: pub})
    wkeys = Witness.create(tmp_path / "witness", trust)
    trust = {**trust, "witness_keys": wkeys}
    return {"home": home, "sk": sk, "kid": kid, "trust": trust, "wdir": tmp_path / "witness",
            "tmp": tmp_path}


def finalize(w, key, value):
    c = Coordinator.open(w["home"])
    st = c.state()
    r = c.submit(make_intent(w["sk"], w["kid"], NS, key, value,
                             expected_generation=st["generation"], expected_root=st["root"]))
    assert r["state"] == "FINALIZED"
    return r["intent_id"]


def journal_path(w):
    return w["home"] / "journal.jsonl"


def rechain(records, start=0, prev=None):
    prev = prev if prev is not None else (records[start - 1]["hash"] if start else "genesis")
    for i in range(start, len(records)):
        r = records[i]
        r["seq"], r["prev"] = i, prev
        r.pop("hash", None)
        r["hash"] = canon.digest(r)
        prev = r["hash"]


def write_journal(w, records):
    journal_path(w).write_bytes(b"".join(canon.jcs(r) + b"\n" for r in records))


def read_journal(w):
    return [json.loads(line) for line in journal_path(w).read_text().splitlines()]


def test_witnessed_bundle_is_w1_in_a_clean_room(world):
    iid = finalize(world, "k1", "v1")
    cp = Witness(world["wdir"]).observe(journal_path(world))
    bundle = Coordinator.open(world["home"]).export_bundle(iid, checkpoint=cp)
    room = world["tmp"] / "room"
    room.mkdir()
    shutil.copy(REPO / "vdb_c1" / "verify.py", room / "verify.py")
    (room / "bundle.json").write_text(json.dumps(bundle))
    (room / "trust_store.json").write_text(json.dumps(world["trust"]))
    out = subprocess.run([sys.executable, "-I", "verify.py", "bundle.json", "trust_store.json",
                          "--require-witness"], cwd=room, capture_output=True, text=True,
                         env={"PATH": os.environ.get("PATH", "")}, timeout=60)
    v = json.loads(out.stdout)
    assert (out.returncode, v["verdict"], v["profile"]) == (0, "VALID_FINALIZED", "W1")


def test_checkpoint_after_later_intents_still_covers_earlier_finality(world):
    first = finalize(world, "k1", "v1")
    finalize(world, "k2", "v2")
    cp = Witness(world["wdir"]).observe(journal_path(world))
    bundle = Coordinator.open(world["home"]).export_bundle(first, checkpoint=cp)
    assert len(bundle["witness"]["journal_suffix"]) == 4
    assert verify_bundle(bundle, world["trust"], require_witness=True)["profile"] == "W1"


def test_unwitnessed_bundle_fails_when_witness_required(world):
    iid = finalize(world, "k1", "v1")
    bundle = Coordinator.open(world["home"]).export_bundle(iid)
    assert verify_bundle(bundle, world["trust"])["profile"] == "L1"
    assert verify_bundle(bundle, world["trust"], require_witness=True)["verdict"] == "REJECTED_NOT_WITNESSED"


def test_history_rewrite_is_refused_and_leaves_signed_fork_evidence(world):
    finalize(world, "k1", "v1")
    w = Witness(world["wdir"])
    cp = w.observe(journal_path(world))
    recs = read_journal(world)
    recs[-1]["at"] = "2030-01-01T00:00:00.000Z"          # finalizer rewrites its own head
    rechain(recs)
    write_journal(world, recs)
    with pytest.raises(WitnessRefused) as e:
        Witness(world["wdir"]).observe(journal_path(world))
    assert e.value.reason == "E_FORK"
    forks = list((world["wdir"] / "forks").iterdir())
    evidence = json.loads(forks[0].read_text())
    assert canon.verify_signed(evidence, world["trust"]["witness_keys"])
    assert evidence["payload"]["witnessed"]["head_hash"] == cp["payload"]["head_hash"]
    assert Witness(world["wdir"]).state["head_hash"] == cp["payload"]["head_hash"]


def test_rollback_and_substituted_journal_are_refused(world):
    finalize(world, "k1", "v1")
    finalize(world, "k2", "v2")
    Witness(world["wdir"]).observe(journal_path(world))
    recs = read_journal(world)
    write_journal(world, recs[:4])                       # drop the second finality
    with pytest.raises(WitnessRefused) as e:
        Witness(world["wdir"]).observe(journal_path(world))
    assert e.value.reason == "E_ROLLBACK"
    other = copy.deepcopy(recs)
    other[0]["at"] = "2030-01-01T00:00:00.000Z"
    rechain(other)
    write_journal(world, other)
    with pytest.raises(WitnessRefused) as e:
        Witness(world["wdir"]).observe(journal_path(world))
    assert e.value.reason == "E_DIFFERENT_JOURNAL"


def test_invalid_finality_is_never_countersigned(world):
    finalize(world, "k1", "v1")
    recs = read_journal(world)
    recs[-1]["body"]["certificate"]["payload"]["post_root"] = "sha256:" + "0" * 64
    rechain(recs)
    write_journal(world, recs)
    w = Witness(world["wdir"])
    with pytest.raises(WitnessRefused) as e:
        w.observe(journal_path(world))
    assert e.value.reason == "E_INVALID_FINALITY"
    assert w.checkpoints() == [] and Witness(world["wdir"]).state["seq"] is None


def test_torn_journal_is_refused_not_repaired(world):
    finalize(world, "k1", "v1")
    data = journal_path(world).read_bytes() + b'{"partial'
    journal_path(world).write_bytes(data)
    with pytest.raises(WitnessRefused) as e:
        Witness(world["wdir"]).observe(journal_path(world))
    assert e.value.reason == "E_TORN_JOURNAL"
    assert journal_path(world).read_bytes() == data      # the witness never writes the journal


def test_forked_bundle_with_old_checkpoint_is_rejected(world):
    iid = finalize(world, "k1", "v1")
    finalize(world, "k2", "v2")
    cp = Witness(world["wdir"]).observe(journal_path(world))
    bundle = Coordinator.open(world["home"]).export_bundle(iid, checkpoint=cp)
    suffix = bundle["witness"]["journal_suffix"]
    suffix[-1]["at"] = "2030-01-01T00:00:00.000Z"        # a different history after the finality
    prev = bundle["journal"][-1]["hash"]
    for r in suffix:                                     # re-chained, so internally consistent
        r["prev"] = prev
        r.pop("hash")
        r["hash"] = canon.digest(r)
        prev = r["hash"]
    assert verify_bundle(bundle, world["trust"])["verdict"] == "REJECTED_FORK"


def test_untrusted_or_edited_checkpoint_is_rejected(world):
    iid = finalize(world, "k1", "v1")
    cp = Witness(world["wdir"]).observe(journal_path(world))
    bundle = Coordinator.open(world["home"]).export_bundle(iid, checkpoint=cp)
    no_witness = {k: v for k, v in world["trust"].items() if k != "witness_keys"}
    assert verify_bundle(bundle, no_witness)["verdict"] == "REJECTED_UNTRUSTED_KEY"
    edited = copy.deepcopy(bundle)
    edited["witness"]["checkpoint"]["payload"]["seq"] += 1
    assert verify_bundle(edited, world["trust"])["verdict"] == "REJECTED_BAD_SIGNATURE"


def test_witness_state_survives_restart_and_only_moves_forward(world):
    finalize(world, "k1", "v1")
    a = Witness(world["wdir"]).observe(journal_path(world))
    b = Witness(world["wdir"]).observe(journal_path(world))   # nothing new: same head
    assert a["payload"]["seq"] == b["payload"]["seq"]
    finalize(world, "k2", "v2")
    c = Witness(world["wdir"]).observe(journal_path(world))
    assert c["payload"]["seq"] > b["payload"]["seq"]
    assert [x["payload"]["seq"] for x in Witness(world["wdir"]).checkpoints()] == [3, 3, 7]
