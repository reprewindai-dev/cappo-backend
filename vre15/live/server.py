"""Live demo: residency follows authority, watched in real time.

Four tenants each hold real state in a governed memory region (copy-on-write over
their own image file, see vre15/revoke.py). While a tenant holds authority it works:
it writes into its region, and the page counts below come straight from the kernel's
page tables (/proc/self/pagemap), sampled four times a second.

Revoking a tenant calls revoke.revoke(): its modified pages are written back to its
image and synced, every private page is dropped, and the server signs a receipt
(Ed25519) saying what was written back and that nothing was left in memory. The page
verifies that receipt in the browser against the published key. Granting again maps
the saved image back: the work survives, only its residency followed authority.

What this shows and what it does not: the page counts and the receipt are real, on
this machine. The supervisor that decides grant and revoke is this demo server, not
production CAPPO, and the signing key is a demo key created at start-up.

    python -m vre15.live.server --port 8080      (Linux; needs /proc/self/pagemap)
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import random
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vre15 import revoke

PAGES = 512                      # 2 MiB per tenant
SIZE = PAGES * revoke.PAGE
MAP_ANON = 0x20
WRITE_HZ = 25                    # records written per second while authorized
NAMES = ["Billing agent", "Support agent", "Research agent", "Ops agent"]
HERE = Path(__file__).resolve().parent

_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class Tenant:
    def __init__(self, idx: int, workdir: Path) -> None:
        self.idx = idx
        self.name = NAMES[idx]
        self.image = workdir / f"tenant-{idx}.img"
        seed = f"tenant {idx} initial state ".encode()
        self.image.write_bytes((seed * (SIZE // len(seed) + 1))[:SIZE])
        self.base = _libc.mmap(None, SIZE, revoke.PROT_RW, revoke.MAP_PRIVATE | MAP_ANON, -1, 0)
        if not self.base or self.base == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), "mmap reservation failed")
        revoke.map_image(self.base, SIZE, self.image)
        self.lock = threading.Lock()
        self.grant: dict | None = None
        self.writes = 0
        self.refused = 0

    def write_one(self, rng: random.Random) -> bool:
        with self.lock:
            if self.grant is None:
                self.refused += 1
                return False
            page = rng.randrange(PAGES)
            msg = f"{self.name}: record {self.writes} at {time.time():.3f}".encode()
            ctypes.memmove(self.base + page * revoke.PAGE, msg, len(msg))
            self.writes += 1
            self.grant["done"] += 1
            return True

    def task_finished(self) -> bool:
        with self.lock:
            return self.grant is not None and self.grant["done"] >= self.grant["task_records"]

    def sample(self) -> dict:
        with self.lock:
            private = len(revoke.modified_pages(self.base, SIZE))
            held = len(revoke.held_privately(self.base, SIZE))
            return {"idx": self.idx, "name": self.name, "authorized": self.grant is not None,
                    "grant_id": self.grant["grant_id"] if self.grant else None,
                    "task_records": self.grant["task_records"] if self.grant else None,
                    "task_done": self.grant["done"] if self.grant else None,
                    "private_pages": private, "held_pages": held, "pages": PAGES,
                    "writes": self.writes, "refused": self.refused}


class Demo:
    def __init__(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="vre15-live-"))
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.kid = "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()
        self.jwk = {"kty": "OKP", "crv": "Ed25519", "kid": self.kid,
                    "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}
        self.tenants = [Tenant(i, self.workdir) for i in range(len(NAMES))]
        self.events: list[dict] = []
        self.receipts: dict[str, dict] = {}
        self.cv = threading.Condition()
        self.rng = random.Random()
        threading.Thread(target=self._work, daemon=True).start()

    def event(self, kind: str, tenant: Tenant, text: str, **extra) -> None:
        with self.cv:
            self.events.append({"t": time.time(), "kind": kind, "tenant": tenant.idx,
                                "name": tenant.name, "text": text, **extra})
            del self.events[:-60]
            self.cv.notify_all()

    def _work(self) -> None:
        while True:
            for t in self.tenants:
                if t.grant is not None:
                    t.write_one(self.rng)
                    if t.task_finished():
                        # Task-bound authority (design rule 1): the task is the grant's whole
                        # scope, so finishing it ends the authority and releases the memory.
                        self.revoke(t.idx, reason="task complete")
            time.sleep(1 / WRITE_HZ)

    def grant(self, idx: int, task_records: int = 600) -> dict:
        t = self.tenants[idx]
        with t.lock:
            if t.grant is not None:
                return {"ok": False, "reason": "already authorized"}
            t.grant = {"grant_id": "grt_" + uuid.uuid4().hex[:12], "granted_at": time.time(),
                       "task": f"process {task_records} records", "task_records": task_records, "done": 0}
        self.event("grant", t, f"authority granted for one task: {t.grant['task']} ({t.grant['grant_id']})")
        return {"ok": True, **t.grant}

    def revoke(self, idx: int, reason: str = "revoked by operator") -> dict:
        t = self.tenants[idx]
        with t.lock:
            if t.grant is None:
                return {"ok": False, "reason": "not authorized"}
            grant, t.grant = t.grant, None
            before = len(revoke.modified_pages(t.base, SIZE))
            r = revoke.revoke(t.base, SIZE, t.image)
            after = len(revoke.held_privately(t.base, SIZE))
        payload = {
            "receipt": "vre15.live.revocation/v1",
            "receipt_id": "rvk_" + uuid.uuid4().hex[:12],
            "tenant": t.name,
            "grant_id": grant["grant_id"],
            "task": grant["task"],
            "records_done": grant["done"],
            "authority_ended_because": reason,
            "granted_at": round(grant["granted_at"], 3),
            "revoked_at": round(time.time(), 3),
            "private_pages_before": before,
            "pages_written_back": r.pages,
            "written_back_sha256": r.digest,
            "pages_left_in_memory": after,
            "image_sha256": hashlib.sha256(t.image.read_bytes()).hexdigest(),
            "revoke_ms": round(r.ms, 2),
            "kid": self.kid,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        receipt = {"payload_b64": _b64(body), "signature_b64": _b64(self.key.sign(body)), "kid": self.kid}
        self.receipts[payload["receipt_id"]] = receipt
        self.event("revoke", t, f"authority ended ({reason}): {r.pages} pages saved to its image, "
                                f"{after} left in memory", receipt_id=payload["receipt_id"])
        return {"ok": True, "receipt": receipt, "payload": payload}

    def attempt(self, idx: int) -> dict:
        t = self.tenants[idx]
        ok = t.write_one(self.rng)
        if ok:
            self.event("write", t, "wrote one record (authorized)")
        else:
            self.event("refused", t, "tried to write without authority: refused")
        return {"ok": ok}

    def state(self) -> dict:
        with self.cv:
            ev = list(self.events[-25:])
        return {"tenants": [t.sample() for t in self.tenants], "events": ev, "t": time.time()}


DEMO: Demo


class Handler(BaseHTTPRequestHandler):
    server_version = "vre15-live"

    def log_message(self, *a) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/key":
            return self._json({"keys": [DEMO.jwk]})
        if self.path == "/state":
            return self._json(DEMO.state())
        if self.path.startswith("/receipt/"):
            r = DEMO.receipts.get(self.path.rsplit("/", 1)[1])
            return self._json(r) if r else self._json({"error": "not found"}, 404)
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"data: " + json.dumps(DEMO.state()).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError):
                return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] in ("grant", "revoke", "attempt") and parts[1].isdigit():
            idx = int(parts[1])
            if 0 <= idx < len(DEMO.tenants):
                return self._json(getattr(DEMO, parts[0])(idx))
        self._json({"error": "not found"}, 404)


def main() -> None:
    global DEMO
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    DEMO = Demo()
    print(f"vre15 live demo on http://{args.host}:{args.port}  (key {DEMO.kid[:30]}...)", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
