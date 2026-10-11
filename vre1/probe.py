#!/usr/bin/env python3
"""VRE-1 measurement probe. Runs in its own process; prints one JSON line.

  probe.py make SPEC_JSON       build one state, snapshot it in every codec
  probe.py make-many SPEC_JSON  build N tenant states, snapshot each
  probe.py restore SPEC_JSON    restore one snapshot when told to (see below)

`restore` imports the runtime first and prints READY. It does nothing more
until the parent writes "go" on stdin, so interpreter start-up is never
counted and the parent can drop the page cache in between (cold restore).
After the restore it serves one request (step(1)) and checks the result
against the digest and memory SHA-256 the original instance produced after
the same request. A restore that is fast but wrong counts as an integrity
failure, not a sample.
"""

import hashlib
import json
import os
import resource
import sys
import time

import wasmtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)          # run with -I: only this directory, nothing ambient
import snapshot as snap  # noqa: E402

WAT = os.path.join(HERE, "workloads", "stateful.wat")


def rss_kib(field="VmRSS"):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(field + ":"):
                return int(line.split()[1])
    raise RuntimeError(f"no {field}")


def cpu_s():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def ms(t0):
    return (time.perf_counter() - t0) * 1000


def module_sha():
    with open(WAT, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build(engine, module, pages, density, seed, steps):
    store = wasmtime.Store(engine)
    x = wasmtime.Instance(store, module, []).exports(store)
    x["fill"](store, pages, density, seed)
    x["step"](store, steps)
    return store, x


def make(spec):
    engine = wasmtime.Engine()
    t = time.perf_counter()
    module = wasmtime.Module.from_file(engine, WAT)
    compile_ms = ms(t)
    with open(spec["cwasm"], "wb") as f:
        f.write(module.serialize())
    t = time.perf_counter()
    wasmtime.Module.deserialize_file(engine, spec["cwasm"])
    deserialize_ms = ms(t)
    base = rss_kib()
    t = time.perf_counter()
    store, x = build(engine, module, spec["pages"], spec["density"], spec["seed"], spec["steps"])
    build_ms = ms(t)
    hot_rss = rss_kib() - base
    digest = x["digest"](store)
    msha = snap.memory_sha256(store, x)
    codecs = {}
    for codec in snap.CODECS:
        c0, t = cpu_s(), time.perf_counter()
        blob = snap.snapshot(store, x, module_sha(), codec)
        encode_ms = ms(t)
        path = os.path.join(spec["dir"], f"state.{codec}")
        t2 = time.perf_counter()
        snap.write_file(path, blob)
        codecs[codec] = {"path": path, "bytes": len(blob), "encode_ms": encode_ms,
                         "write_fsync_ms": ms(t2), "snapshot_ms": ms(t), "cpu_ms": (cpu_s() - c0) * 1000}
    x["step"](store, 1)                                   # the reference "next request"
    out = {"compile_ms": compile_ms, "deserialize_ms": deserialize_ms, "build_ms": build_ms,
           "hot_rss_kib": hot_rss, "logical_bytes": x["memory"].data_len(store),
           "digest_at_snapshot": digest, "memory_sha256_at_snapshot": msha,
           "digest_after_next": x["digest"](store),
           "memory_sha256_after_next": snap.memory_sha256(store, x), "codecs": codecs}
    print(json.dumps(out))


def make_many(spec):
    engine = wasmtime.Engine()
    module = wasmtime.Module.from_file(engine, WAT)
    with open(spec["cwasm"], "wb") as f:
        f.write(module.serialize())
    tenants = []
    for i in range(spec["tenants"]):
        store, x = build(engine, module, spec["pages"], spec["density"], 1000 + i, spec["steps"])
        blob = snap.snapshot(store, x, module_sha(), spec["codec"])
        path = os.path.join(spec["dir"], f"tenant-{i:04d}.snap")
        snap.write_file(path, blob)
        x["step"](store, 1)
        tenants.append({"path": path, "bytes": len(blob), "digest_after_next": x["digest"](store),
                        "memory_sha256_after_next": snap.memory_sha256(store, x)})
    print(json.dumps({"tenants": tenants, "logical_bytes_each": spec["pages"] * snap.PAGE}))


def restore(spec):
    engine = wasmtime.Engine()
    msha = module_sha()
    base, base_hwm = rss_kib(), rss_kib("VmHWM")
    print("READY", flush=True)
    if sys.stdin.readline().strip() != "go":
        return
    c0 = cpu_s()
    t0 = time.perf_counter()
    with open(spec["path"], "rb") as f:
        blob = bytearray(os.fstat(f.fileno()).st_size)
        f.readinto(blob)
    read_ms = ms(t0)
    t = time.perf_counter()
    snap.verify_payload(blob)
    verify_ms = ms(t)
    t = time.perf_counter()
    header, runs = snap.decode(blob, verify=False)
    decode_ms = ms(t)
    t = time.perf_counter()
    module = wasmtime.Module.deserialize_file(engine, spec["cwasm"])
    module_ms = ms(t)
    t = time.perf_counter()
    store = wasmtime.Store(engine)
    x = wasmtime.Instance(store, module, []).exports(store)
    instantiate_ms = ms(t)
    t = time.perf_counter()
    snap.restore_into(store, x, header, runs, msha)
    write_ms = ms(t)
    restore_ms = ms(t0)
    restore_cpu_ms = (cpu_s() - c0) * 1000
    t = time.perf_counter()
    x["step"](store, 1)
    first_request_us = ms(t) * 1000
    bytes_written = sum(len(d) for _, d in runs)
    peak_delta = rss_kib("VmHWM") - base_hwm
    del blob, runs                       # transient buffers; the instance keeps its own memory
    settled_delta = rss_kib() - base
    ok = (x["digest"](store) == spec["digest_after_next"]
          and snap.memory_sha256(store, x) == spec["memory_sha256_after_next"])
    print(json.dumps({"restore_ms": restore_ms, "read_ms": read_ms, "verify_ms": verify_ms,
                      "decode_ms": decode_ms,
                      "module_ms": module_ms, "instantiate_ms": instantiate_ms,
                      "write_ms": write_ms, "restore_cpu_ms": restore_cpu_ms,
                      "first_request_us": first_request_us, "rss_peak_delta_kib": peak_delta,
                      "rss_settled_delta_kib": settled_delta, "integrity_ok": ok,
                      "bytes_written": bytes_written}))


def pack(spec):
    """Restore K tenants into K stores that share one engine and one module,
    all live at once in this process. Separate stores keep each tenant's
    linear memory and globals separate; only the runtime and code are shared."""
    engine = wasmtime.Engine()
    msha = module_sha()
    module = wasmtime.Module.deserialize_file(engine, spec["cwasm"])
    base = rss_kib()
    live, restore_ms, failures = [], [], 0
    for t in spec["tenants"]:
        t0 = time.perf_counter()
        with open(t["path"], "rb") as f:
            blob = bytearray(os.fstat(f.fileno()).st_size)
            f.readinto(blob)
        header, runs = snap.decode(blob)
        store = wasmtime.Store(engine)
        x = wasmtime.Instance(store, module, []).exports(store)
        snap.restore_into(store, x, header, runs, msha)
        restore_ms.append(ms(t0))
        del blob, runs
        live.append((store, x, t))
    resident_kib = rss_kib() - base                     # all K live, buffers freed
    request_us = []
    for store, x, t in live:                            # one request each, then check it
        t0 = time.perf_counter()
        x["step"](store, 1)
        request_us.append(ms(t0) * 1000)
        failures += not (x["digest"](store) == t["digest_after_next"]
                         and snap.memory_sha256(store, x) == t["memory_sha256_after_next"])
    print(json.dumps({"k": len(live), "resident_kib_all_live": resident_kib,
                      "restore_ms": restore_ms, "request_us": request_us,
                      "integrity_failures": failures}))


if __name__ == "__main__":
    {"make": make, "make-many": make_many, "restore": restore, "pack": pack}[sys.argv[1]](
        json.loads(sys.argv[2]))
