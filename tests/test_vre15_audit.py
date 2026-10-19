"""vre15/audit.py: an observer outside the runtime catches residency the
runtime's own evidence does not admit.

Four small runtimes, each a real process holding one tenant region mapped
copy-on-write over its image, each audited by a real auditor process that
reads the runtime's page tables from /proc:
- honest: revokes when authority ends (vre15/revoke.py) and journals it;
- liar:   writes the pages back but never drops them, then signs a record
          saying no private page is held;
- late:   revokes honestly, but 1.3 s after authority ended;
- never:  never revokes and keeps no journal (the control arm).
The verifier must accept only the honest one.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

from vre15 import evidence, verify_evidence

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pagemap is Linux only")

DURATION, WINDOW, PERIOD = 3.0, [0.0, 0.5], 0.05

RUNTIME = r"""
import ctypes, hashlib, json, os, struct, sys, time
sys.path.insert(0, sys.argv[2])
from vre15 import evidence, revoke
spec = json.loads(sys.argv[1])
mode, img, d = spec["mode"], spec["image"], spec["dir"]
length = os.path.getsize(img)
fd = os.open(img, os.O_RDONLY)
base = revoke._libc.mmap(None, length, revoke.PROT_RW, revoke.MAP_PRIVATE, fd, 0)
os.close(fd)

def clock(c):
    with open(d + "/clock.tmp", "w") as f:
        json.dump(c, f)
    os.replace(d + "/clock.tmp", d + "/clock.json")

def until(at):
    time.sleep(max(0.0, start + at - time.time()))

journal = None
if mode != "never":
    custody = evidence.custody_grant("audit-test", time.time(), time.time() + 600)
    journal = evidence.Journal(d + "/revocations.jsonl", d + "/issuer.key", custody)
start = time.time()
clock({"start_wall": start})
for i in range(5):                                     # under authority: modify 5 pages
    ctypes.memset(base + i * 3 * revoke.PAGE, 0x41 + i, 100)
until(spec["window"][1] + (1.3 if mode == "late" else 0.02))
if mode in ("honest", "late"):
    t0 = time.time()
    r = revoke.revoke(base, length, img)
    held = len(revoke.held_privately(base, length))
elif mode == "liar":                                   # write back, keep the private pages
    t0 = time.time()
    dirty = revoke.modified_pages(base, length)
    h = hashlib.sha256()
    fd = os.open(img, os.O_RDWR)
    for first, count in revoke.runs_of(dirty):
        data = ctypes.string_at(base + first * revoke.PAGE, count * revoke.PAGE)
        os.pwrite(fd, data, first * revoke.PAGE)
        h.update(struct.pack("<II", first, count))
        h.update(data)
    os.fdatasync(fd)
    os.close(fd)
    r = revoke.Receipt(len(dirty), h.hexdigest(), 0.0, 0, tuple(tuple(x) for x in revoke.runs_of(dirty)))
    held = 0                                           # the lie
if journal is not None:
    journal.record(0, {"class": "interactive", "windows": [spec["window"]]}, spec["window"],
                   start + spec["window"][1], t0, time.time(), r, held, length // revoke.PAGE, 0, "t0.img")
    journal.close()
until(spec["duration"])
clock({"start_wall": start, "end_wall": time.time()})
"""


def run_audited(tmp, mode):
    d = tmp / mode
    (d / "images").mkdir(parents=True)
    img = d / "images" / "t0.img"
    img.write_bytes(b"\0" * (64 * 4096))
    issuer = evidence.new_issuer(d / "issuer.key") if mode != "never" else None
    trace = d / "trace.json"
    trace.write_text(json.dumps({"tenants": 1, "duration_s": DURATION, "requests": [],
                                 "envelopes": [{"class": "interactive", "windows": [WINDOW]}]}))
    spec = {"mode": mode, "image": str(img), "dir": str(d), "window": WINDOW, "duration": DURATION}
    rt = subprocess.Popen([sys.executable, "-c", RUNTIME, json.dumps(spec), str(ROOT)])
    aspec = {"pid": rt.pid, "images_dir": str(d / "images"), "trace": str(trace), "clock": str(d / "clock.json"),
             "out": str(d / "audit.jsonl"), "pub": str(d / "auditor.pub.json"), "period_s": PERIOD}
    au = subprocess.Popen([sys.executable, "-I", str(ROOT / "vre15" / "audit.py"), json.dumps(aspec)])
    return d, issuer, rt, au


@pytest.fixture(scope="module")
def audited(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("audit")
    runs = {m: run_audited(tmp, m) for m in ("honest", "liar", "late", "never")}   # in parallel
    out = {}
    for m, (d, issuer, rt, au) in runs.items():
        assert rt.wait(timeout=30) == 0 and au.wait(timeout=30) == 0, m
        trust = {"auditor_keys": json.loads((d / "auditor.pub.json").read_text())}
        records = None
        if issuer is not None:
            trust["issuer_keys"] = issuer
            records = evidence.read_journal(d / "revocations.jsonl")
        out[m] = (records, trust, verify_evidence.read_jsonl(d / "audit.jsonl"), d)
    return out


def verdict(audited, mode, audit=None):
    records, trust, lines, _ = audited[mode]
    return verify_evidence.verify_bundle(evidence.bundle(records, None), trust,
                                         audit=lines if audit is None else audit, require_audit=True)


def test_honest_runtime_is_valid_and_the_gap_was_actually_watched(audited):
    v = verdict(audited, "honest")
    assert v["verdict"] == "VALID" and v["profile"] == "L1+A1", v
    a = v["audit"]
    assert a["after_authority"]["gaps_checked"] == 1 and a["after_authority"]["gaps_with_private_pages"] == 0
    assert a["sweeps"] >= 20 and a["contradicted_records"] == 0


def test_a_runtime_that_keeps_pages_after_signing_their_release_is_caught(audited):
    records, *_ = audited["liar"]
    assert records[0]["body"]["post_condition"]["held_privately"] == 0          # what it signed
    assert verify_evidence.verify_bundle(evidence.bundle(records, None), audited["liar"][1])["verdict"] == "VALID"
    v = verdict(audited, "liar")                                                # what the kernel showed
    assert v["verdict"] == "REJECTED_RESIDENCY_CONTRADICTED", v
    assert "5 private pages of tenant 0" in v["detail"]


def test_a_late_revocation_is_residency_that_outlived_authority(audited):
    v = verdict(audited, "late")
    assert v["verdict"] == "REJECTED_RESIDENCY_OUTLIVED_AUTHORITY", v


def test_the_control_arm_is_reported_from_the_audit_alone(audited):
    _, trust, lines, _ = audited["never"]
    a = verify_evidence.audit_only(lines, trust)
    assert a["verdict"] == "AUDIT_READ" and a["audit_verdict"] == "PRIVATE_STATE_OUTSIDE_AUTHORITY", a
    assert a["after_authority"]["max_private_pages"] == 5


def test_a_tampered_or_unsealed_or_foreign_audit_log_is_refused(audited):
    _, trust, lines, _ = audited["liar"]
    hidden = [dict(r) for r in lines]
    for r in hidden[1:-1]:
        r["held"] = {}                                   # erase what the auditor saw
    assert verdict(audited, "liar", hidden)["verdict"] == "REJECTED_AUDIT_INCONSISTENT"
    assert verdict(audited, "liar", lines[:-1])["verdict"] == "REJECTED_AUDIT_INCOMPLETE"
    records, trust, lines, _ = audited["honest"]
    foreign = dict(trust, auditor_keys={})
    v = verify_evidence.verify_bundle(evidence.bundle(records, None), foreign, audit=lines, require_audit=True)
    assert v["verdict"] == "REJECTED_UNTRUSTED_KEY"
    assert verify_evidence.verify_bundle(evidence.bundle(records, None), trust,
                                         require_audit=True)["verdict"] == "REJECTED_NOT_AUDITED"


def test_the_cli_checks_an_audit_from_an_empty_directory(audited, tmp_path):
    records, trust, _, d = audited["honest"]
    (tmp_path / "verify_evidence.py").write_bytes((ROOT / "vre15" / "verify_evidence.py").read_bytes())
    (tmp_path / "b.json").write_text(json.dumps(evidence.bundle(records, None)))
    (tmp_path / "t.json").write_text(json.dumps(trust))
    (tmp_path / "a.jsonl").write_bytes((d / "audit.jsonl").read_bytes())
    p = subprocess.run([sys.executable, "-I", "verify_evidence.py", "b.json", "t.json", "--audit", "a.jsonl",
                        "--require-audit"], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["profile"] == "L1+A1", p.stdout + p.stderr


# -- the pre-registered verdict (vre15/audit_prereg.py), on synthetic manifests

def _gaps(checkable, private):
    return {"gaps": checkable, "gaps_checkable": checkable, "gaps_checked": checkable,
            "checkable_gaps_with_private_pages": private, "gaps_with_private_pages": private}


def _row(seed, policy, after_private=0, before_private=150, ok=True):
    audit = {"after_authority": _gaps(140, after_private), "before_first_authority": _gaps(190, before_private)}
    r = {"seed": seed, "policy": policy, "exit_code": 0 if ok else -9, "oom_kills": 0}
    if policy == "mapped-ckpt":
        r["evidence"] = {"verdict": "VALID", "profile": "W1+A1", "audit": audit}
    else:
        r["audit"] = {"verdict": "AUDIT_READ", **audit}
    return r


def _manifest(rows, record_ws):
    return {"run_id": "x", "params": {"audit": True, "record_ws": record_ws, "budget_mib": [16]},
            "summary": {"vre15": rows}}


def _pair():
    from vre15 import audit_prereg as P
    ws = [_row(s, "mapped-ckpt") for s in P.SEEDS] + [_row(s, "mapped", after_private=120) for s in P.SEEDS]
    plain = [_row(s, "mapped-ckpt", before_private=0) for s in P.SEEDS]
    return ws, plain


def _outcomes(ws, plain):
    from vre15 import audit_prereg as P
    v = P.summarize(_manifest(ws, True), _manifest(plain, False))
    return tuple(v[k]["outcome"] for k in ("A1_treatment_clean_under_independent_audit",
                                           "A2_auditor_sees_the_control", "A3_warmup_state_precedes_authority"))


def test_prereg_confirms_only_when_every_rule_holds():
    ws, plain = _pair()
    assert _outcomes(ws, plain) == ("confirmed",) * 3
    ws[0]["evidence"]["audit"]["after_authority"]["gaps_checked"] = 139      # one gap unwatched
    assert _outcomes(ws, plain)[0] == "not confirmed"
    ws, plain = _pair()
    ws[5]["audit"]["after_authority"]["checkable_gaps_with_private_pages"] = 69   # control mostly clean
    assert _outcomes(ws, plain)[1] == "not confirmed"
    ws, plain = _pair()
    plain[2]["evidence"]["audit"]["before_first_authority"]["gaps_with_private_pages"] = 1
    assert _outcomes(ws, plain)[2] == "not confirmed"
    ws, plain = _pair()
    ws[3]["exit_code"] = -9                                                  # a killed run fails its rule
    assert _outcomes(ws, plain)[0] == "not confirmed"
    ws, plain = _pair()                                                      # a missing run fails all three
    from vre15 import audit_prereg as P
    v = P.summarize(_manifest(ws[:-1], True), _manifest(plain, False))
    assert v["problems"] and all(v[k]["outcome"] == "not confirmed" for k in v if k.startswith("A"))
