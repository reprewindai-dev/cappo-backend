"""Revocation evidence (vre15/evidence.py), checked by an independent offline
verifier (vre15/verify_evidence.py) and by CAPPO's own EEEVerifier.

Revocations here are real: real file-backed regions, real write-back and
remap through vre15/revoke.py, the post-condition measured from pagemap.
"""

import copy
import ctypes
import json
import os
import subprocess
import sys
import time

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Linux only", allow_module_level=True)

from cappo_backend.services.eee import EEEVerifier, VerificationVerdict  # noqa: E402
from vdb_c1 import canon  # noqa: E402
from vre15 import evidence, revoke, verify_evidence  # noqa: E402

PAGE = revoke.PAGE
N = 8
MAP_ANON = 0x20
_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
_libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]


def resident(base, length):
    vec = ctypes.create_string_buffer(length // PAGE)
    return sum(b & 1 for b in vec.raw) if _libc.mincore(base, length, vec) == 0 else None


@pytest.fixture
def world(tmp_path):
    """Two tenants with mapped regions, a runtime journal, a witness."""
    images, regions = tmp_path / "images", {}
    images.mkdir()
    for t in (0, 1):
        img = images / f"t{t}.img"
        img.write_bytes(bytes([t + 1]) * PAGE + bytes(PAGE * (N - 1)))
        base = _libc.mmap(None, N * PAGE, revoke.PROT_RW, revoke.MAP_PRIVATE | MAP_ANON, -1, 0)
        revoke.map_image(base, N * PAGE, img)
        regions[t] = (base, img)
    issuer = evidence.new_issuer(tmp_path / "issuer.key")
    t0 = time.time()
    custody = evidence.custody_grant("test-run", t0 - 60, t0 + 3600)
    journal = evidence.Journal(tmp_path / "revocations.jsonl", tmp_path / "issuer.key", custody)
    authority = {t: {"class": "interactive", "windows": [[1.0, 2.0], [5.0, 6.0]]} for t in (0, 1)}
    w = {"tmp": tmp_path, "images": images, "regions": regions, "issuer": issuer, "journal": journal,
         "authority": authority, "t0": t0, "witness_dir": tmp_path / "witness"}
    w["witness_keys"] = evidence.Witness.create(w["witness_dir"], issuer)
    yield w
    journal.close()
    for base, _ in regions.values():
        _libc.munmap(base, N * PAGE)


def revoke_one(w, t, pages, byte, window, via="record"):
    base, img = w["regions"][t]
    for p in pages:
        ctypes.memset(base + p * PAGE, byte, 128)
    ended = w["t0"] + window[1]
    started = max(time.time(), ended)
    r = revoke.revoke(base, N * PAGE, img)
    done = max(time.time(), started)
    return getattr(w["journal"], via)(t, w["authority"][t], window, ended, started, done, r,
                               len(revoke.held_privately(base, N * PAGE)), N * PAGE // PAGE,
                               resident(base, N * PAGE), img.name)


def history(w):
    revoke_one(w, 0, [1, 2], 0x11, [1.0, 2.0])
    revoke_one(w, 1, [3], 0x22, [1.0, 2.0])
    revoke_one(w, 0, [2, 6], 0x33, [5.0, 6.0])         # the second window rewrites page 2
    records = evidence.read_journal(w["journal"].path)
    cp = evidence.Witness(w["witness_dir"]).observe(w["journal"].path, w["images"])
    return records, cp


def trust(w):
    return {"issuer_keys": w["issuer"], "witness_keys": w["witness_keys"]}


def verdict(w, b, images=True):
    return verify_evidence.verify_bundle(b, trust(w), w["images"] if images else None, require_witness=True)


def test_real_revocations_verify_offline_with_witness_and_images(world):
    records, cp = history(world)
    v = verdict(world, evidence.bundle(records, cp))
    assert v["verdict"] == "VALID" and v["profile"] == "W1", v
    assert (v["revocations"], v["tenants"], v["images_checked"]) == (3, 2, 2)
    assert set(cp["payload"]["images"]) == {"t0.img", "t1.img"}
    assert v["pages_written"] == 5
    assert all(r["body"]["post_condition"]["held_privately"] == 0 for r in records)


def test_cappo_eee_verifier_accepts_every_revocation_envelope(world):
    records, _ = history(world)
    cappo = EEEVerifier({kid: canon.unb64u(pub) for kid, pub in world["issuer"].items()})
    for rec in records:
        report = cappo.verify(rec["body"]["envelope"])
        assert report.verdict == VerificationVerdict.VALID_WITH_UNRESOLVED_REFS, report
        assert report.reasons == ["REVOCATION_NOT_CHECKED"]   # true: the custody grant has no revocation list


def test_cli_verifies_from_an_empty_directory(world, tmp_path):
    records, cp = history(world)
    out = tmp_path / "offline"
    out.mkdir()
    (out / "bundle.json").write_text(json.dumps(evidence.bundle(records, cp)))
    (out / "trust.json").write_text(json.dumps(trust(world)))
    src = os.path.join(os.path.dirname(verify_evidence.__file__), "verify_evidence.py")
    (out / "verify_evidence.py").write_bytes(open(src, "rb").read())
    p = subprocess.run([sys.executable, "-I", "verify_evidence.py", "bundle.json", "trust.json",
                        "--images", str(world["images"]), "--require-witness"],
                       cwd=out, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["verdict"] == "VALID"


def resign(w, rec):
    """Re-sign an edited record's envelope with the real issuer key, so only
    the property under test can fail, never the signature."""
    sk, kid = evidence.load_key(w["tmp"] / "issuer.key")
    env = rec["body"]["envelope"]
    body = {k: v for k, v in env.items() if k not in ("envelope_hash", "previous_envelope_hash", "signatures")}
    env["envelope_hash"] = canon.digest(body)
    env["signatures"][0]["value"] = canon.b64u(sk.sign(env["envelope_hash"].encode("ascii")))


def rechain(records):
    prev = "genesis"
    for i, rec in enumerate(records):
        rec["seq"], rec["prev"] = i, prev
        rec["hash"] = canon.digest({k: v for k, v in rec.items() if k != "hash"})
        prev = rec["hash"]


def test_tampering_is_rejected_with_a_specific_reason(world):
    records, cp = history(world)

    def reject(edit, reason, images=True, witness=True, fix=None):
        rs = copy.deepcopy(records)
        edit(rs)
        if fix:
            fix(rs)
        v = verdict(world, evidence.bundle(rs, cp if witness else None), images)
        assert v["verdict"] == reason, (reason, v)

    def drop_middle(rs):
        del rs[1]

    def swap_order(rs):
        rs[0], rs[1] = rs[1], rs[0]

    def retained(rs):
        rs[0]["body"]["post_condition"]["held_privately"] = 3

    def retained_signed(rs):
        retained(rs)
        e = rs[0]["body"]["envelope"]
        rc = rs[0]["body"]["receipt"]
        e["output_commitment"] = canon.digest({"receipt_digest": rc["digest"], "runs": rc["runs"],
                                               "image": rs[0]["body"]["image"]["name"],
                                               "held_privately": 3})
        resign(world, rs[0])

    def early(rs):
        ended = canon.parse_time(rs[0]["body"]["revocation"]["started_at"]) + 1
        rs[0]["body"]["authority"]["ended_at"] = canon.format_time(ended)

    def fewer_pages(rs):
        rs[2]["body"]["receipt"]["runs"] = [[2, 1]]
        rs[2]["body"]["receipt"]["pages"] = 1

    def envelope_edit(rs):
        rs[0]["body"]["envelope"]["status"] = "completed "

    reject(drop_middle, "REJECTED_JOURNAL_INCONSISTENT")
    reject(drop_middle, "REJECTED_FORK", fix=rechain)                # a re-chained history vs the witness
    reject(swap_order, "REJECTED_FORK", fix=rechain)
    reject(retained, "REJECTED_ENVELOPE_MISMATCH", fix=rechain)      # record edited, envelope not
    reject(retained_signed, "REJECTED_RESIDENCY_RETAINED", witness=False, fix=rechain)
    reject(early, "REJECTED_REVOKED_UNDER_AUTHORITY", witness=False, fix=rechain)
    reject(fewer_pages, "REJECTED_ENVELOPE_MISMATCH", images=False, witness=False,
           fix=rechain)                                              # runs are bound by the signed envelope
    reject(envelope_edit, "REJECTED_ENVELOPE_HASH", fix=rechain)

    assert verdict(world, evidence.bundle(records, None))["verdict"] == "REJECTED_NOT_WITNESSED"
    other = {"issuer_keys": evidence.new_issuer(world["tmp"] / "other.key"), "witness_keys": world["witness_keys"]}
    assert verify_evidence.verify_bundle(evidence.bundle(records, cp), other)["verdict"] == "REJECTED_UNTRUSTED_KEY"


@pytest.mark.parametrize("page", [3, 7])
def test_an_image_changed_after_it_was_witnessed_is_detected(world, page):
    """Page 3 is covered by tenant 1's last receipt; page 7 by nothing the
    runtime wrote, only by the witness's hash of the final image."""
    records, cp = history(world)
    img = world["regions"][1][1]
    data = bytearray(img.read_bytes())
    data[page * PAGE] ^= 0xFF
    img.write_bytes(bytes(data))
    v = verdict(world, evidence.bundle(records, cp))
    assert v["verdict"] == "REJECTED_IMAGE_MISMATCH", v
    assert verdict(world, evidence.bundle(records, cp), images=False)["verdict"] == "VALID"


def test_background_writer_produces_the_same_verifiable_journal(world, tmp_path):
    custody = evidence.custody_grant("bg-run", world["t0"] - 60, world["t0"] + 3600)
    world["journal"].close()
    world["journal"] = evidence.Journal(tmp_path / "bg.jsonl", tmp_path / "issuer.key", custody, background=True)
    revoke_one(world, 0, [1], 0x55, [1.0, 2.0], via="submit")
    revoke_one(world, 1, [2, 3], 0x66, [1.0, 2.0], via="submit")
    world["journal"].close()
    world["journal"] = evidence.Journal(tmp_path / "unused.jsonl", tmp_path / "issuer.key", custody)
    records = evidence.read_journal(tmp_path / "bg.jsonl")
    v = verify_evidence.verify_bundle(evidence.bundle(records, None), trust(world), world["images"])
    assert v["verdict"] == "VALID" and v["revocations"] == 2, v


def test_witness_only_moves_forward_and_records_fork_evidence(world):
    history(world)
    path = world["journal"].path
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:2]))                            # roll back one record
    with pytest.raises(evidence.WitnessRefused) as e:
        evidence.Witness(world["witness_dir"]).observe(path)
    assert e.value.reason == "E_ROLLBACK"
    forks = list((world["witness_dir"] / "forks").iterdir())
    assert len(forks) == 1
    fork = json.loads(forks[0].read_text())
    assert canon.verify_signed(fork, world["witness_keys"]) and fork["payload"]["reason"] == "E_ROLLBACK"


def test_witness_refuses_to_countersign_an_invalid_record(world):
    revoke_one(world, 0, [1], 0x44, [1.0, 2.0])
    path = world["journal"].path
    rec = json.loads(path.read_text())
    rec["body"]["post_condition"]["held_privately"] = 1
    rec["hash"] = canon.digest({k: v for k, v in rec.items() if k != "hash"})
    path.write_bytes(canon.jcs(rec) + b"\n")
    with pytest.raises(evidence.WitnessRefused) as e:
        evidence.Witness(world["witness_dir"]).observe(path)
    assert e.value.reason == "E_INVALID_RECORD"
