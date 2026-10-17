#!/usr/bin/env python3
"""Residency auditor: watches the runtime's page tables from outside it.

    python -I vre15/audit.py SPEC_JSON

The evidence in vre15/evidence.py is the runtime's own word: the runtime
measures `held_privately` and signs it. This process is a second observer
that does not take that word. It runs as its own process, outside the
tenant cgroup, imports nothing from the runtime, and holds an Ed25519 key
that exists only in its memory. Every sweep it:

1. reads /proc/<pid>/maps and finds each tenant's governed region as the
   kernel lists it: the mappings of that tenant's image file, `t<i>.img`;
2. reads /proc/<pid>/pagemap over those mappings and counts private pages:
   present and not a file page (copy-on-write copies), or swapped. Those are
   tenant state the image does not hold;
3. appends one hash-chained record: when the sweep ran, which tenants had
   a mapped region, and the private-page count of every tenant above zero.

It does not judge. It records the kernel's view and the authority
schedule it was given, and signs the head when the run ends. The offline
verifier (vre15/verify_evidence.py, `verify_audit`) joins the two: private
pages of a tenant outside its authority windows, past a grace period, are
residency that outlived authority, and any seen after the runtime's signed
"released" record are a contradiction of that record.

Spec: pid, images_dir, trace (the authority schedule, from the trace
file), clock (a file the runtime writes: start_wall, and end_wall when its
request loop ends), out (the audit log), pub (where to write the public
trust entry), period_s, run_id.

What it cannot see: copies of tenant state anywhere other than the image
mappings, and anything a process with the same root privileges does to
this process. Linux only; reading another process's pagemap needs ptrace
read access to it (root here).
"""

import base64
import hashlib
import json
import os
import signal
import sys
import time

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

AUDIT = "veklom.vre15.residency-audit/v1"
PAGE = 4096
# Top byte of a pagemap entry: bit 63 present (0x80), 62 swapped (0x40),
# 61 file page or shared anonymous (0x20). Private: swapped, or present and
# not a file page.
PRIVATE = bytes(1 if (b & 0x40) or (b & 0x80 and not b & 0x20) else 0 for b in range(256))


def digest(obj):
    return "sha256:" + hashlib.sha256(rfc8785.dumps(obj)).hexdigest()


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def regions(pid, images_dir):
    """tenant -> [(start, end)] of every mapping of its image file."""
    prefix = images_dir.rstrip("/") + "/"
    out = {}
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.split(maxsplit=5)
            if len(parts) < 6:
                continue
            path = parts[5].rstrip("\n")
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if not (name.startswith("t") and name.endswith(".img") and name[1:-4].isdigit()):
                continue
            a, b = (int(x, 16) for x in parts[0].split("-"))
            out.setdefault(int(name[1:-4]), []).append((a, b))
    return out


def private_pages(pagemap, spans):
    n = 0
    for a, b in spans:
        pagemap.seek(a // PAGE * 8)
        raw = pagemap.read((b - a) // PAGE * 8)
        n += raw[7::8].translate(PRIVATE).count(1)
    return n


def bitmap(tenants, n):
    bits = bytearray((n + 7) // 8)
    for t in tenants:
        if 0 <= t < n:
            bits[t // 8] |= 1 << (t % 8)
    return bits.hex()


class Log:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.seq, self.prev = 0, "genesis"

    def append(self, rec, sk=None):
        rec = {"protocol": AUDIT, "seq": self.seq, "prev": self.prev, **rec}
        rec["hash"] = digest(rec)
        if sk is not None:
            rec["sig"] = b64u(sk.sign(rec["hash"].encode("ascii")))
        self.f.write(rfc8785.dumps(rec) + b"\n")
        self.f.flush()
        self.seq, self.prev = self.seq + 1, rec["hash"]


def read_clock(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv):
    spec = json.loads(argv[1])
    pid, period = int(spec["pid"]), float(spec.get("period_s", 0.25))
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append("terminated"))

    sk = Ed25519PrivateKey.generate()                   # never written anywhere
    pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    kid = "ed25519:" + hashlib.sha256(pub).hexdigest()[:16]
    with open(spec["pub"], "w") as f:
        json.dump({kid: b64u(pub)}, f)

    with open(spec["trace"], "rb") as f:
        raw_trace = f.read()
    trace = json.loads(raw_trace)
    n = trace["tenants"]

    clock = None
    while not stop:                                     # the runtime's trace start
        clock = read_clock(spec["clock"])
        if clock and "start_wall" in clock:
            break
        if not os.path.exists(f"/proc/{pid}"):
            stop.append("process_exited_before_start")
            break
        time.sleep(0.005)
    observed_start = time.time()
    log = Log(spec["out"])
    log.append({"kind": "HEADER", "run_id": spec.get("run_id"), "auditor_kid": kid, "pid": pid,
                "images_dir": spec["images_dir"], "tenants": n, "duration_s": trace["duration_s"],
                "trace_sha256": hashlib.sha256(raw_trace).hexdigest(),
                "schedule": [e["windows"] for e in trace["envelopes"]],
                "period_s": period, "page_size": PAGE,
                "declared_start_wall": clock.get("start_wall") if clock else None,
                "observed_start_at": round(observed_start, 6)})

    sweeps, end, observed_end = 0, None, None
    pagemap = None
    try:
        pagemap = open(f"/proc/{pid}/pagemap", "rb", buffering=0) if not stop else None
        next_at = time.time()
        while not stop:
            clock = read_clock(spec["clock"])
            if clock and "end_wall" in clock:
                end, observed_end = clock["end_wall"], time.time()
                break
            t0 = time.time()
            try:
                regs = regions(pid, spec["images_dir"])
                held = {}
                for t, spans in sorted(regs.items()):
                    c = private_pages(pagemap, spans)
                    if c:
                        held[str(t)] = c
            except (FileNotFoundError, ProcessLookupError, OSError):
                stop.append("process_exited")
                break
            t1 = time.time()
            log.append({"kind": "SWEEP", "t0": round(t0, 6), "t1": round(t1, 6),
                        "mapped": bitmap(regs, n), "held": held})
            sweeps += 1
            next_at += period
            time.sleep(max(0.0, next_at - time.time()))
    finally:
        if pagemap is not None:
            pagemap.close()
        log.append({"kind": "SEAL", "sweeps": sweeps, "end_reason": stop[0] if stop else "runtime_end_marker",
                    "declared_end_wall": end,
                    "observed_end_at": round(observed_end, 6) if observed_end else round(time.time(), 6),
                    "auditor_kid": kid}, sk)
        os.fsync(log.f.fileno())
        log.f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
