#!/usr/bin/env python3
"""Offline verifier for VRE-1.5 revocation evidence.

    python verify_evidence.py bundle.json trust_store.json [--images DIR] [--require-witness]
                              [--audit audit.jsonl [--require-audit]]
    python verify_evidence.py --audit-only audit.jsonl trust_store.json

Imports nothing from the runtime: the canonical encoding, the journal chain,
the EEE envelope hash and signature, and the receipt digest are re-derived
here. Needs only `rfc8785` and `cryptography`. Prints one JSON verdict and
exits 0 only for VALID.

What a VALID verdict establishes, for every revocation in the journal:
- the runtime's issuer key signed an EEE envelope for it, and the envelope
  matches the journal record it sits in;
- the journal is one unbroken hash chain;
- the revocation started no earlier than the moment the tenant's authority
  window ended;
- right after it, the runtime measured no page of the tenant's region
  present in or swapped from its process (pagemap), and said so in a
  signed record;
- with --require-witness, a pinned witness countersigned a head covering
  the whole journal, so records cannot be dropped, reordered or rewritten
  later without the fork showing;
- with --images, each revoked tenant's last receipt digest is recomputed
  from the bytes its image holds at the pages it wrote; and when the witness
  attested the final images, every image file is byte-identical to the
  witness's hash of it.

Without --audit it does not establish that the runtime measured honestly.
The issuer key belongs to the runtime; the witness checks history, not
memory. That is evidence level E2: the supervisor is trusted.

With --audit, the log of the residency auditor (vre15/audit.py) is joined
in. The auditor is a separate process outside the tenant cgroup, with its
own key, that read the runtime's page tables from /proc during the run. A
VALID verdict then also establishes, for the image-backed regions:
- no record's claim of release is contradicted: no sweep that ran wholly
  after a record's completion and before the tenant's next window saw a
  private page of that tenant (REJECTED_RESIDENCY_CONTRADICTED);
- no private page of a tenant was seen more than GRACE_S after one of its
  windows ended and before its next one began
  (REJECTED_RESIDENCY_OUTLIVED_AUTHORITY);
- the auditor's log is one hash chain, sealed by a pinned auditor key, and
  covers the run from trace start to the runtime's end marker.
Private pages held before a tenant's first window are reported, not
rejected: the journal makes no claim about them. The auditor and the
runtime still share a machine and root, so this is independence of the
observation (kernel page tables read by another process under another
key), not of administrative control: evidence level E3.

--audit-only checks an auditor log on its own, for runs without a journal
(the control arm), and reports what it saw.
"""

import base64
import hashlib
import json
import pathlib
import struct
import sys
from datetime import datetime

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

JOURNAL = "veklom.vre15.revocation-journal/v1"
BUNDLE = "veklom.vre15.revocation-bundle/v1"
CHECKPOINT = "veklom.vre15.revocation-checkpoint/v1"
AUDIT = "veklom.vre15.residency-audit/v1"
PAGE = 4096
GRACE_S = 1.0       # residency may outlive a window by this much (revocation lag); fixed before any audited run
EPS_S = 0.010       # clock-read ordering between the runtime and the auditor
CLOCK_TOL_S = 1.0   # the runtime's declared trace start/end vs when the auditor saw them
MIN_GAP_S = 1.0     # a gap shorter than this after the grace is not counted as checkable
INTEGRITY = ("envelope_hash", "previous_envelope_hash", "signatures")


class Reject(Exception):
    def __init__(self, verdict, check, detail=""):
        super().__init__(detail)
        self.verdict, self.check, self.detail = verdict, check, detail


def digest(obj):
    return "sha256:" + hashlib.sha256(rfc8785.dumps(obj)).hexdigest()


def unb64u(text):
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != text:
        raise ValueError("non-canonical base64url")
    return raw


def ts(text):
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError(f"bad timestamp {text!r}")
    return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()


def _key(trusted, kid, what):
    if kid not in trusted:
        raise Reject("REJECTED_UNTRUSTED_KEY", what, f"kid {kid} is not in the trust store")
    pub = unb64u(trusted[kid])
    if "ed25519:" + hashlib.sha256(pub).hexdigest()[:16] != kid:
        raise Reject("REJECTED_BAD_SIGNATURE", what, "kid does not name this key")
    return Ed25519PublicKey.from_public_bytes(pub)


def check_envelope(env, trusted):
    """EEE v0.1.0 integrity: envelope hash, then the issuer's signature on it."""
    body = {k: v for k, v in env.items() if k not in INTEGRITY}
    if env.get("hash_alg") != "SHA-256" or env.get("envelope_hash") != digest(body):
        raise Reject("REJECTED_ENVELOPE_HASH", "envelope_hash", "envelope_hash does not match its body")
    for sig in env.get("signatures", []):
        kid = sig.get("kid")
        if sig.get("scheme") == "Ed25519" and sig.get("signer") == f"{env['issuer']}#{kid}":
            try:
                _key(trusted, kid, "envelope_signature").verify(unb64u(sig["value"]),
                                                                env["envelope_hash"].encode("ascii"))
                return
            except Reject:
                raise
            except Exception:
                pass
    raise Reject("REJECTED_BAD_SIGNATURE", "envelope_signature", "no valid issuer signature")


def check_record(rec, trusted):
    """One revocation record: its envelope, and that the two agree."""
    b = rec["body"]
    env = b["envelope"]
    check_envelope(env, trusted)
    t, rc, post = rec["tenant"], b["receipt"], b["post_condition"]
    pages = sum(c for _, c in rc["runs"])
    prev_end = -1
    for first, count in rc["runs"]:
        if first <= prev_end or count < 1 or first + count > post["region_pages"]:
            raise Reject("REJECTED_RECEIPT_MALFORMED", "receipt", f"run {first}+{count}")
        prev_end = first + count - 1
    if pages != rc["pages"]:
        raise Reject("REJECTED_RECEIPT_MALFORMED", "receipt", "page count does not match its runs")
    expect_output = digest({"receipt_digest": rc["digest"], "runs": rc["runs"],
                            "image": b["image"]["name"], "held_privately": post["held_privately"]})
    if (env["principal"] != {"scheme": "veklom-tenant", "identifier": f"tenant-{t}"}
            or env["output_commitment"] != expect_output
            or env["input_commitment"] != digest({"tenant_authority": b["authority"]["digest"],
                                                  "window": b["authority"]["window"]})
            or env["started_at"] != b["revocation"]["started_at"]
            or env["ended_at"] != b["revocation"]["completed_at"]
            or [a["action_hash"] for a in env["tool_actions"]] != ["sha256:" + rc["digest"]]):
        raise Reject("REJECTED_ENVELOPE_MISMATCH", "binding", f"seq {rec['seq']}")
    if ts(b["revocation"]["started_at"]) < ts(b["authority"]["ended_at"]):
        raise Reject("REJECTED_REVOKED_UNDER_AUTHORITY", "ordering",
                     f"seq {rec['seq']}: revocation started before the tenant's window ended")
    if ts(b["revocation"]["completed_at"]) < ts(b["revocation"]["started_at"]):
        raise Reject("REJECTED_TIME", "ordering", f"seq {rec['seq']}")
    if post["held_privately"] != 0 or env["status"] != "completed":
        raise Reject("REJECTED_RESIDENCY_RETAINED", "residency",
                     f"seq {rec['seq']}: {post['held_privately']} private pages held after authority ended")


def receipt_digest_from_image(data, runs):
    h = hashlib.sha256()
    for first, count in runs:
        h.update(struct.pack("<II", first, count))
        h.update(data[first * PAGE:(first + count) * PAGE])
    return h.hexdigest()


def _merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _mapped_set(hexbits, n):
    raw = bytes.fromhex(hexbits)
    return {t for t in range(min(n, 8 * len(raw))) if raw[t // 8] >> (t % 8) & 1}


def verify_audit(lines, trust, records=None, grace_s=GRACE_S):
    """Join an auditor log with the authority schedule (and, if given, the
    runtime's revocation records). Raises Reject on a broken log; returns
    what the auditor saw otherwise. See the module docstring."""
    prev = "genesis"
    for i, rec in enumerate(lines):
        body = {k: v for k, v in rec.items() if k not in ("hash", "sig")}
        if (rec.get("protocol") != AUDIT or rec.get("seq") != i or rec.get("prev") != prev
                or rec.get("hash") != digest(body)):
            raise Reject("REJECTED_AUDIT_INCONSISTENT", "audit_chain", f"record {i}")
        prev = rec["hash"]
    if len(lines) < 2 or lines[0].get("kind") != "HEADER" or lines[-1].get("kind") != "SEAL" or any(
            r.get("kind") != "SWEEP" for r in lines[1:-1]):
        raise Reject("REJECTED_AUDIT_INCOMPLETE", "audit_structure", "header, sweeps, seal expected")
    head, seal, sweeps = lines[0], lines[-1], lines[1:-1]
    if seal.get("auditor_kid") != head.get("auditor_kid"):
        raise Reject("REJECTED_AUDIT_INCONSISTENT", "audit_seal", "seal and header name different keys")
    _key(trust.get("auditor_keys", {}), seal["auditor_kid"], "auditor_signature").verify(
        unb64u(seal.get("sig", "")), seal["hash"].encode("ascii"))

    start, end = head.get("declared_start_wall"), seal.get("declared_end_wall")
    if start is None or abs(head["observed_start_at"] - start) > CLOCK_TOL_S:
        raise Reject("REJECTED_AUDIT_CLOCK", "audit_start", "declared trace start differs from when it appeared")
    if end is None or end < start + head["duration_s"] - 2.0:
        raise Reject("REJECTED_AUDIT_INCOMPLETE", "audit_end",
                     f"audit ended ({seal.get('end_reason')}) before the trace did")
    if abs(seal["observed_end_at"] - end) > CLOCK_TOL_S:
        raise Reject("REJECTED_AUDIT_CLOCK", "audit_end", "declared end differs from when it appeared")
    if not sweeps:
        raise Reject("REJECTED_AUDIT_INCOMPLETE", "audit_sweeps", "no sweeps")
    schedule = head["schedule"]

    if records:
        for r in records:
            t, w = r["tenant"], r["body"]["authority"]["window"]
            if w not in schedule[t] or abs(ts(r["body"]["authority"]["ended_at"]) - (start + w[1])) > 0.002:
                raise Reject("REJECTED_AUDIT_SCHEDULE_MISMATCH", "audit_schedule",
                             f"seq {r['seq']}: window {w} of tenant {t} is not the audited schedule's")

    # Gaps without authority, per tenant, from trace start to the end marker.
    # "pre": before the tenant's first window; "post": after one ended,
    # starting `grace_s` after the window's end.
    gaps = []
    for t, windows in enumerate(schedule):
        merged = _merge([[start + s - EPS_S, start + e] for s, e in windows])
        cursor, kind = start, "pre"
        for a, b in merged + [[end, None]]:
            lo = cursor + (grace_s if kind == "post" else 0.0)
            if a > lo:
                gaps.append({"tenant": t, "kind": kind, "lo": lo, "hi": a})
            if b is None:
                break
            cursor, kind = max(cursor, b), "post"
    by_tenant = {}
    for g in gaps:
        g.update(sweeps_inside=0, violations=0, max_private=0)
        by_tenant.setdefault(g["tenant"], []).append(g)

    vanished, seen_mapped = 0, set()
    for sw in sweeps:
        held = {int(k): v for k, v in sw["held"].items()}
        mapped = _mapped_set(sw["mapped"], head["tenants"])
        vanished += len(seen_mapped - mapped)
        seen_mapped = mapped
        for t, gs in by_tenant.items():
            for g in gs:
                if not (g["lo"] < sw["t0"] and sw["t1"] < g["hi"]):
                    continue
                g["sweeps_inside"] += 1
                if held.get(t, 0) > 0:
                    g["violations"] += 1
                    g["max_private"] = max(g["max_private"], held[t])

    contradictions = []
    for r in records or []:
        t, c = r["tenant"], ts(r["body"]["revocation"]["completed_at"])
        nxt = min([start + s for s, _ in schedule[t] if start + s > c] + [end])
        for sw in sweeps:
            if c + EPS_S < sw["t0"] and sw["t1"] < nxt - EPS_S and sw["held"].get(str(t), 0) > 0:
                contradictions.append({"seq": r["seq"], "tenant": t, "sweep_t0": sw["t0"],
                                       "private_pages": sw["held"][str(t)]})
                break

    def summary(kind):
        # Coverage counts gaps long enough to check; violations count in every gap.
        every = [g for g in gaps if g["kind"] == kind]
        long = [g for g in every if g["hi"] - g["lo"] >= MIN_GAP_S]
        return {"gaps": len(every), "gaps_checkable": len(long),
                "gaps_checked": sum(1 for g in long if g["sweeps_inside"]),
                "checkable_gaps_with_private_pages": sum(1 for g in long if g["violations"]),
                "gaps_with_private_pages": sum(1 for g in every if g["violations"]),
                "violation_sweeps": sum(g["violations"] for g in every),
                "max_private_pages": max([g["max_private"] for g in every] + [0])}

    periods = sorted(b["t0"] - a["t0"] for a, b in zip(sweeps, sweeps[1:]))
    durations = sorted(sw["t1"] - sw["t0"] for sw in sweeps)
    post, pre = summary("post"), summary("pre")
    return {"auditor_kid": seal["auditor_kid"], "sweeps": len(sweeps), "grace_s": grace_s,
            "sweep_period_ms_p50": round(1000 * periods[len(periods) // 2], 1) if periods else None,
            "sweep_ms_p50": round(1000 * durations[len(durations) // 2], 1),
            "sweep_ms_max": round(1000 * durations[-1], 1),
            "after_authority": post, "before_first_authority": pre,
            "contradicted_records": len(contradictions), "contradictions": contradictions[:10],
            "regions_vanished": vanished,
            "audit_verdict": "CLEAN" if not (post["gaps_with_private_pages"] or contradictions or vanished)
            else "PRIVATE_STATE_OUTSIDE_AUTHORITY"}


def verify_bundle(bundle, trust, images_dir=None, require_witness=False, audit=None, require_audit=False):
    passed = []
    try:
        if bundle.get("protocol") != BUNDLE:
            raise Reject("REJECTED_INCOMPLETE_BUNDLE", "structure", "not a VRE-1.5 revocation bundle")
        records = bundle["journal"]
        if not records:
            raise Reject("REJECTED_INCOMPLETE_BUNDLE", "structure", "empty journal")
        prev, last_done, ids = "genesis", None, set()
        for seq, rec in enumerate(records):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if (rec.get("protocol") != JOURNAL or rec.get("seq") != seq or rec.get("prev") != prev
                    or rec.get("kind") != "REVOKED" or rec.get("hash") != digest(body)):
                raise Reject("REJECTED_JOURNAL_INCONSISTENT", "chain", f"record {seq}")
            prev = rec["hash"]
            done = ts(rec["body"]["revocation"]["completed_at"])
            if last_done is not None and done < last_done:
                raise Reject("REJECTED_TIME", "ordering", f"record {seq} completes before record {seq - 1}")
            last_done = done
            eid = rec["body"]["envelope"]["execution_id"]
            if eid in ids:
                raise Reject("REJECTED_DUPLICATE", "chain", f"execution_id {eid} repeated")
            ids.add(eid)
        passed.append("chain")
        for rec in records:
            check_record(rec, trust["issuer_keys"])
        passed += ["envelopes", "binding", "ordering", "residency"]

        profile = "L1"
        if "witness" in bundle:
            cp = bundle["witness"]["checkpoint"]
            p = cp["payload"]
            _key(trust.get("witness_keys", {}), cp.get("kid"), "witness_signature").verify(
                unb64u(cp["sig"]), rfc8785.dumps(p))
            if (p.get("protocol") != CHECKPOINT or p["seq"] != len(records) - 1
                    or p["head_hash"] != records[-1]["hash"] or p["journal_genesis_hash"] != records[0]["hash"]):
                raise Reject("REJECTED_FORK", "witness_checkpoint",
                             "this journal is not the history the witness countersigned")
            passed.append("witness")
            profile = "W1"
        if require_witness and profile != "W1":
            raise Reject("REJECTED_NOT_WITNESSED", "witness", "no witness checkpoint in the bundle")

        images_checked = 0
        if images_dir is not None:
            last = {}
            for rec in records:
                last[rec["tenant"]] = rec
            for t, rec in sorted(last.items()):
                b = rec["body"]
                data = (pathlib.Path(images_dir) / b["image"]["name"]).read_bytes()
                if receipt_digest_from_image(data, b["receipt"]["runs"]) != b["receipt"]["digest"]:
                    raise Reject("REJECTED_IMAGE_MISMATCH", "images",
                                 f"tenant {t}: image does not hold what its last revocation wrote")
            attested = bundle.get("witness", {}).get("checkpoint", {}).get("payload", {}).get("images")
            if attested is not None:
                for name, sha in sorted(attested.items()):
                    if hashlib.sha256((pathlib.Path(images_dir) / name).read_bytes()).hexdigest() != sha:
                        raise Reject("REJECTED_IMAGE_MISMATCH", "images",
                                     f"{name} differs from the image the witness attested")
                    images_checked += 1
            passed.append("images")

        audited = None
        if audit is not None:
            audited = verify_audit(audit, trust, records)
            if audited["contradicted_records"]:
                c = audited["contradictions"][0]
                raise Reject("REJECTED_RESIDENCY_CONTRADICTED", "audit",
                             f"seq {c['seq']}: auditor saw {c['private_pages']} private pages of tenant "
                             f"{c['tenant']} after the record said they were released")
            if audited["after_authority"]["gaps_with_private_pages"]:
                raise Reject("REJECTED_RESIDENCY_OUTLIVED_AUTHORITY", "audit",
                             f"{audited['after_authority']['gaps_with_private_pages']} gaps with private pages "
                             f"more than {GRACE_S} s after authority ended")
            if audited["regions_vanished"]:
                raise Reject("REJECTED_AUDIT_REGION_VANISHED", "audit",
                             "a governed region disappeared from the runtime's mappings")
            passed.append("audit")
            profile += "+A1"
        elif require_audit:
            raise Reject("REJECTED_NOT_AUDITED", "audit", "no auditor log given")
    except Reject as r:
        return {"verdict": r.verdict, "failed_check": r.check, "detail": r.detail, "checks_passed": passed}
    except Exception as e:
        return {"verdict": "REJECTED_INCOMPLETE_BUNDLE", "failed_check": "structure",
                "detail": f"{type(e).__name__}: {e}", "checks_passed": passed}
    lags = sorted(1000 * (ts(r["body"]["revocation"]["completed_at"]) - ts(r["body"]["authority"]["ended_at"]))
                  for r in records)
    return {"verdict": "VALID", "profile": profile, "revocations": len(records),
            "tenants": len({r["tenant"] for r in records}),
            "pages_written": sum(r["body"]["receipt"]["pages"] for r in records),
            "images_checked": images_checked,
            "lag_ms_p50": round(lags[len(lags) // 2], 1), "lag_ms_max": round(lags[-1], 1),
            **({"audit": audited} if audited is not None else {}),
            "checks_passed": passed}


def read_jsonl(path):
    data = pathlib.Path(path).read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("log does not end with a complete record")
    return [json.loads(line) for line in data.splitlines()]


def audit_only(lines, trust):
    """An auditor log on its own (a run without a journal)."""
    try:
        return {"verdict": "AUDIT_READ", **verify_audit(lines, trust)}
    except Reject as r:
        return {"verdict": r.verdict, "failed_check": r.check, "detail": r.detail}
    except Exception as e:
        return {"verdict": "REJECTED_AUDIT_INCOMPLETE", "failed_check": "structure",
                "detail": f"{type(e).__name__}: {e}"}


def main(argv):
    args = list(argv[1:])
    if args[:1] == ["--audit-only"] and len(args) == 3:
        result = audit_only(read_jsonl(args[1]), json.loads(pathlib.Path(args[2]).read_text()))
        print(json.dumps(result, indent=1))
        return 0 if result["verdict"] == "AUDIT_READ" else 1
    flags = {a for a in args if a in ("--require-witness", "--require-audit")}
    args = [a for a in args if a not in flags]
    opts = {}
    for opt in ("--images", "--audit"):
        if opt in args:
            i = args.index(opt)
            opts[opt] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 2:
        print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
        return 2
    bundle = json.loads(pathlib.Path(args[0]).read_text())
    trust = json.loads(pathlib.Path(args[1]).read_text())
    audit = read_jsonl(opts["--audit"]) if "--audit" in opts else None
    result = verify_bundle(bundle, trust, opts.get("--images"), "--require-witness" in flags,
                           audit, "--require-audit" in flags)
    print(json.dumps(result, indent=1))
    return 0 if result["verdict"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
