#!/usr/bin/env python3
"""Offline verifier for VRE-1.5 revocation evidence.

    python verify_evidence.py bundle.json trust_store.json [--images DIR] [--require-witness]

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

What it does not establish: that the runtime measured honestly. The
issuer key belongs to the runtime; the witness checks history, not memory.
That is evidence level E2: the supervisor is trusted.
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
PAGE = 4096
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


def verify_bundle(bundle, trust, images_dir=None, require_witness=False):
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
            "checks_passed": passed}


def main(argv):
    args = list(argv[1:])
    require = "--require-witness" in args
    args = [a for a in args if a != "--require-witness"]
    images = None
    if "--images" in args:
        i = args.index("--images")
        images = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    bundle = json.loads(pathlib.Path(args[0]).read_text())
    trust = json.loads(pathlib.Path(args[1]).read_text())
    result = verify_bundle(bundle, trust, images, require)
    print(json.dumps(result, indent=1))
    return 0 if result["verdict"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
