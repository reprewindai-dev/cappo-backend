"""VDB-C1.0 command line.

  python -m vdb_c1.cli keygen --out authority.key
  python -m vdb_c1.cli init --home H --namespace NS --authority-key authority.key
  python -m vdb_c1.cli state --home H
  python -m vdb_c1.cli sign-intent --key authority.key --home H --set KEY VALUE --out intent.json
  python -m vdb_c1.cli submit --home H --intent intent.json [--crash-at POINT]
  python -m vdb_c1.cli recover --home H
  python -m vdb_c1.cli export --home H --intent-id ID --out bundle.json [--witness-dir W]
  python -m vdb_c1.cli witness-init --dir W --home H     (pins H's keys; adds W's key to H/trust_store.json)
  python -m vdb_c1.cli witness-observe --dir W --home H  (countersign the journal head, or refuse)
  python vdb_c1/verify.py bundle.json H/trust_store.json [--require-witness]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vdb_c1 import canon
from vdb_c1.coordinator import CRASH_POINTS, Coordinator, init_home, load_key, make_intent, save_key
from vdb_c1.witness import Witness, WitnessRefused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vdb_c1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", type=pathlib.Path, required=True)
    i = sub.add_parser("init")
    i.add_argument("--home", type=pathlib.Path, required=True)
    i.add_argument("--namespace", required=True)
    i.add_argument("--authority-key", type=pathlib.Path, action="append", required=True,
                   help="authority key file; only its public half is used")
    for name in ("state", "recover"):
        sub.add_parser(name).add_argument("--home", type=pathlib.Path, required=True)
    s = sub.add_parser("sign-intent")
    s.add_argument("--key", type=pathlib.Path, required=True)
    s.add_argument("--home", type=pathlib.Path, required=True,
                   help="read the current generation and root from this store")
    s.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), required=True)
    s.add_argument("--ttl", type=int, default=300)
    s.add_argument("--out", type=pathlib.Path, required=True)
    m = sub.add_parser("submit")
    m.add_argument("--home", type=pathlib.Path, required=True)
    m.add_argument("--intent", type=pathlib.Path, required=True)
    m.add_argument("--crash-at", choices=CRASH_POINTS)
    e = sub.add_parser("export")
    e.add_argument("--home", type=pathlib.Path, required=True)
    e.add_argument("--intent-id", required=True)
    e.add_argument("--out", type=pathlib.Path, required=True)
    e.add_argument("--witness-dir", type=pathlib.Path)
    for name in ("witness-init", "witness-observe"):
        w = sub.add_parser(name)
        w.add_argument("--dir", type=pathlib.Path, required=True)
        w.add_argument("--home", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)

    if a.cmd == "keygen":
        print(json.dumps({"kid": save_key(a.out, Ed25519PrivateKey.generate())}))
    elif a.cmd == "init":
        keys = {}
        for path in a.authority_key:
            sk, kid = load_key(path)
            keys[kid] = canon.b64u(canon.public_raw(sk))
        print(json.dumps(init_home(a.home, a.namespace, keys), indent=1))
    elif a.cmd == "state":
        print(json.dumps(Coordinator.open(a.home).state(), indent=1))
    elif a.cmd == "recover":
        print(json.dumps(Coordinator.open(a.home).recover(), indent=1))
    elif a.cmd == "sign-intent":
        sk, kid = load_key(a.key)
        st = Coordinator.open(a.home).state()
        signed = make_intent(sk, kid, st["namespace"], a.set[0], a.set[1],
                             expected_generation=st["generation"], expected_root=st["root"],
                             ttl_s=a.ttl)
        a.out.write_text(json.dumps(signed, indent=1))
        print(json.dumps({"intent_id": signed["payload"]["intent_id"]}))
    elif a.cmd == "submit":
        signed = json.loads(a.intent.read_text())
        print(json.dumps(Coordinator.open(a.home, crash_at=a.crash_at).submit(signed)))
    elif a.cmd == "export":
        c = Coordinator.open(a.home)
        cp = None
        if a.witness_dir:
            fin = next(r for r in c.journal.for_intent(a.intent_id) if r["kind"] == "FINALIZED")
            cp = Witness(a.witness_dir).covering(fin["seq"])
            if cp is None:
                print("no witness checkpoint covers this finality yet", file=sys.stderr)
                return 1
        a.out.write_text(json.dumps(c.export_bundle(a.intent_id, checkpoint=cp), indent=1))
    elif a.cmd == "witness-init":
        trust_path = a.home / "trust_store.json"
        trust = json.loads(trust_path.read_text())
        trust.setdefault("witness_keys", {}).update(Witness.create(a.dir, trust))
        trust_path.write_text(json.dumps(trust, indent=1))
        print(json.dumps(trust["witness_keys"]))
    elif a.cmd == "witness-observe":
        try:
            print(json.dumps(Witness(a.dir).observe(a.home / "journal.jsonl")))
        except WitnessRefused as e:
            print(json.dumps({"refused": e.reason, "detail": str(e)}))
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
