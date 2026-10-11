#!/usr/bin/env python3
"""VDB-C1.0 offline verifier.

    python verify.py bundle.json trust_store.json

Runs from a directory that holds nothing but this file, the bundle and the
public trust store. Imports nothing from the coordinator package; the
canonical encoding, signatures, journal chain, state-machine replay, state
roots and certificate binding are all re-derived here from PROTOCOL.md.
Prints one JSON verdict; exits 0 only for VALID_FINALIZED.
"""

import base64
import hashlib
import json
import sys
from datetime import datetime

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

JOURNAL = "veklom.vdb.c1.journal/v1"
BUNDLE = "veklom.vdb.c1.bundle/v1"
FINALITY = "veklom.vdb.c1.finality/v1"
INTENT = "veklom.vdb.c1.intent/v1"
CLOCK_SKEW_S = 5
BINDING = ("PREPARED", "COMMITTING", "COMMIT_UNCERTAIN", "COMMITTED", "FINALIZED", "ABORTED")
LEGAL_FINAL_PATHS = (
    ("PREPARED", "COMMITTING", "COMMITTED", "FINALIZED"),
    ("PREPARED", "COMMITTING", "COMMIT_UNCERTAIN", "COMMITTED", "FINALIZED"),
)


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


def check_signed(signed, trusted, what):
    kid = signed.get("kid")
    if kid not in trusted:
        raise Reject("REJECTED_UNTRUSTED_KEY", what, f"kid {kid} is not in the trust store")
    try:
        pub = unb64u(trusted[kid])
        if "ed25519:" + hashlib.sha256(pub).hexdigest()[:16] != kid or signed.get("alg") != "Ed25519":
            raise ValueError("kid does not name this key")
        Ed25519PublicKey.from_public_bytes(pub).verify(unb64u(signed["sig"]),
                                                       rfc8785.dumps(signed["payload"]))
    except Exception as e:
        raise Reject("REJECTED_BAD_SIGNATURE", what, str(e) or "signature does not verify")


def state_root(namespace, generation, entries):
    return digest({"protocol": "veklom.vdb.c1.state/v1", "namespace": namespace,
                   "generation": generation, "entries": sorted(entries)})


def ts(text):
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError(f"bad timestamp {text!r}")
    return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()


def verify_bundle(bundle, trust):
    passed = []
    try:
        _verify(bundle, trust, passed)
    except Reject as r:
        return {"verdict": r.verdict, "failed_check": r.check, "detail": r.detail,
                "checks_passed": passed}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
        return {"verdict": "REJECTED_INCOMPLETE_BUNDLE", "failed_check": "structure",
                "detail": f"{type(e).__name__}: {e}", "checks_passed": passed}
    return {"verdict": "VALID_FINALIZED", "intent_id": bundle["intent_id"],
            "post_root": bundle["receipt"]["certificate"]["payload"]["post_root"],
            "checks_passed": passed}


def _verify(bundle, trust, passed):
    # 1. structure
    if bundle.get("protocol") != BUNDLE:
        raise Reject("REJECTED_INCOMPLETE_BUNDLE", "structure", "not a C1 bundle")
    iid, journal, receipt = bundle["intent_id"], bundle["journal"], bundle["receipt"]
    cert = receipt["certificate"]
    authority, finalizers = trust["authority_keys"], trust["finalizer_keys"]
    if not isinstance(journal, list) or not journal:
        raise Reject("REJECTED_INCOMPLETE_BUNDLE", "structure", "empty journal")
    passed.append("structure")

    # 2. journal chain from genesis
    prev = "genesis"
    for i, rec in enumerate(journal):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if (rec.get("protocol") != JOURNAL or rec.get("seq") != i or rec.get("prev") != prev
                or rec.get("hash") != digest(body)):
            raise Reject("REJECTED_JOURNAL_INCONSISTENT", "journal_chain", f"record {i}")
        prev = rec["hash"]
    passed.append("journal_chain")

    # 3. one finality, legal path, bundle ends at it
    mine = [r for r in journal if r["intent_id"] == iid and r["kind"] in BINDING]
    kinds = tuple(r["kind"] for r in mine)
    if kinds.count("FINALIZED") > 1 or kinds.count("COMMITTED") > 1 or kinds.count("PREPARED") > 1:
        raise Reject("REJECTED_DUPLICATE_FINALITY", "single_finality", f"path {kinds}")
    if kinds not in LEGAL_FINAL_PATHS:
        raise Reject("REJECTED_JOURNAL_INCONSISTENT", "state_machine", f"path {kinds}")
    if journal[-1] is not mine[-1]:
        raise Reject("REJECTED_JOURNAL_INCONSISTENT", "bundle_prefix", "bundle must end at FINALIZED")
    passed.append("state_machine")
    prepared = mine[0]
    committed = next(r for r in mine if r["kind"] == "COMMITTED")
    finalized = mine[-1]
    pb, cb = prepared["body"], committed["body"]
    signed_intent = pb["signed_intent"]
    intent = signed_intent["payload"]

    # 4. authority and finalizer signatures
    check_signed(signed_intent, authority, "intent_signature")
    if intent["authority_kid"] != signed_intent["kid"] or intent.get("protocol") != INTENT:
        raise Reject("REJECTED_BAD_SIGNATURE", "intent_signature", "kid or protocol mismatch")
    check_signed(cert, finalizers, "certificate_signature")
    passed += ["intent_signature", "certificate_signature"]

    # 5. every record for the intent binds the same signed intent
    ih = digest(intent)
    if intent["intent_id"] != iid or any(r["body"].get("intent_hash") != ih for r in mine):
        raise Reject("REJECTED_JOURNAL_INCONSISTENT", "intent_binding", "intent_hash differs")
    passed.append("intent_binding")

    # 6. admitted inside the authority window
    admitted = ts(pb["admission"]["admitted_at"])
    if not (ts(intent["issued_at"]) - CLOCK_SKEW_S <= admitted < ts(intent["expires_at"])):
        raise Reject("REJECTED_OUTSIDE_AUTHORITY_WINDOW", "authority_window",
                     "PREPARED outside the intent's validity window")
    passed.append("authority_window")

    # 7. roots recompute from their entries
    ns = intent["namespace"]
    if state_root(ns, pb["pre_generation"], pb["pre_entries"]) != pb["pre_root"]:
        raise Reject("REJECTED_ROOT_MISMATCH", "pre_root", "pre_root does not recompute")
    if pb["pre_root"] != intent["expected_root"]:
        raise Reject("REJECTED_ROOT_MISMATCH", "precondition_root", "pre_root != expected_root")
    if state_root(ns, cb["post_generation"], cb["post_entries"]) != cb["post_root"]:
        raise Reject("REJECTED_ROOT_MISMATCH", "post_root", "post_root does not recompute")
    passed.append("state_roots")

    # 8. generation chain
    if pb["pre_generation"] != intent["expected_generation"] or \
            cb["post_generation"] != pb["pre_generation"] + 1:
        raise Reject("REJECTED_GENERATION_MISMATCH", "generation",
                     f"{intent['expected_generation']} -> {pb['pre_generation']} -> {cb['post_generation']}")
    passed.append("generation")

    # 9. the committed state is exactly the signed payload applied once
    commitment = digest({"protocol": "veklom.vdb.c1.payload/v1", "op": intent["op"],
                         "key": intent["key"], "value": intent["value"]})
    expected_post = sorted([e for e in pb["pre_entries"] if e[0] != intent["key"]]
                           + [[intent["key"], intent["value"]]])
    applied = cb["applied_row"]
    if (intent["op"] != "UPSERT" or pb["payload_commitment"] != commitment
            or sorted(cb["post_entries"]) != expected_post
            or applied != {"intent_id": iid, "intent_hash": ih,
                           "generation": cb["post_generation"], "post_root": cb["post_root"]}):
        raise Reject("REJECTED_PAYLOAD_SUBSTITUTION", "payload", "committed state is not the signed UPSERT")
    passed.append("payload")

    # 10. receipt = certificate in the journal = fields re-derived from it
    want = {
        "protocol": FINALITY, "intent_id": iid, "intent_hash": ih, "namespace": ns,
        "authority_kid": intent["authority_kid"], "expected_generation": intent["expected_generation"],
        "pre_root": pb["pre_root"], "payload_commitment": commitment,
        "post_generation": cb["post_generation"], "post_root": cb["post_root"],
        "physical": {**cb["physical"], "applied_row": applied},
        "journal": {"prepared_seq": prepared["seq"], "prepared_hash": prepared["hash"],
                    "committed_seq": committed["seq"], "committed_hash": committed["hash"]},
        "finalized_at": committed["at"], "finalizer_kid": cert["kid"],
    }
    if (finalized["body"]["certificate"] != cert or receipt["certificate_digest"] != digest(cert)
            or cert["payload"] != want):
        raise Reject("REJECTED_RECEIPT_MISMATCH", "receipt_binding",
                     "receipt, certificate and journal do not bind")
    passed.append("receipt_binding")


def main(argv):
    if len(argv) != 3:
        print("usage: verify.py bundle.json trust_store.json", file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        bundle = json.load(f)
    with open(argv[2]) as f:
        trust = json.load(f)
    result = verify_bundle(bundle, trust)
    print(json.dumps(result, indent=1))
    return 0 if result["verdict"] == "VALID_FINALIZED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
