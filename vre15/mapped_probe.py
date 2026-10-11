#!/usr/bin/env python3
"""Probe: can a tenant's linear memory be a copy-on-write mapping of its
snapshot file, instead of an anonymous copy?

    sudo python -m vre15.mapped_probe [--out FILE]

Answers four questions before any policy is built on the idea:

  A. correctness  mmap(MAP_PRIVATE|MAP_FIXED) of a page-aligned raw image
                  over a live wasmtime instance's linear memory gives
                  exactly the snapshotted state (guest digest matches), and
                  later guest steps evolve identically to the original.
  B. isolation    guest writes land in private copy-on-write pages; the
                  image file is unchanged.
  C. charging     touched mapped pages are charged to the faulting cgroup
                  (memory.usage_in_bytes, memory.stat cache/mapped_file),
                  so a policy built on this stays at the same budget.
  D. eviction     inside a cgroup that forbids swap (memsw = limit), a
                  64 MiB mapped tenant can be read end to end through a
                  16 MiB limit: clean pages are dropped without any swap
                  write. The same state as an anonymous copy is the control
                  and must be OOM-killed, since anonymous pages can only be
                  evicted to swap.

Writes a JSON report. Uses vre1/workloads/stateful.wat and the vre1
snapshot module's globals.
"""

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import wasmtime

HERE = pathlib.Path(__file__).resolve().parent
WAT = HERE.parent / "vre1" / "workloads" / "stateful.wat"
PAGE = 4096
WASM_PAGE = 65536
PROT_RW = 0x1 | 0x2
MAP_PRIVATE, MAP_FIXED = 0x02, 0x10

_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]


def instance(engine, module):
    store = wasmtime.Store(engine)
    return store, wasmtime.Instance(store, module, []).exports(store)


def region(store, x):
    mem = x["memory"]
    return ctypes.addressof(mem.data_ptr(store).contents), mem.data_len(store)


def write_image(store, x, path):
    """Page-aligned raw image; all-zero 4 KiB blocks are left as holes."""
    mem = x["memory"]
    raw = bytes(mem.read(store, 0, mem.data_len(store)))
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.ftruncate(fd, len(raw))
        zero = bytes(PAGE)
        for off in range(0, len(raw), PAGE):
            blk = raw[off:off + PAGE]
            if blk != zero:
                os.pwrite(fd, blk, off)
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def map_image(store, x, path, pages):
    mem = x["memory"]
    mem.grow(store, pages - mem.size(store))
    base, length = region(store, x)
    fd = os.open(path, os.O_RDONLY)
    try:
        got = _libc.mmap(base, length, PROT_RW, MAP_PRIVATE | MAP_FIXED, fd, 0)
    finally:
        os.close(fd)                      # the mapping keeps its own reference
    if got != base:
        raise OSError(ctypes.get_errno(), "mmap MAP_FIXED did not land on the linear memory base")
    return base, length


def copy_image(store, x, path, pages):
    mem = x["memory"]
    mem.grow(store, pages - mem.size(store))
    base, length = region(store, x)
    with open(path, "rb") as f:
        data = bytearray(f.read())
    ctypes.memmove(base, ctypes.addressof(ctypes.c_char.from_buffer(data)), length)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def correctness(engine, module, work):
    """A and B, in this process."""
    s0, x0 = instance(engine, module)
    x0["fill"](s0, 64, 250, 42)                   # 4 MiB, 25% non-zero blocks
    x0["step"](s0, 100)
    img = work / "a.img"
    mem_sha = write_image(s0, x0, img)
    before = file_sha(img)
    s1, x1 = instance(engine, module)
    map_image(s1, x1, img, 64)
    for g in ("seed", "steps"):
        x1[g].set_value(s1, x0[g].value(s0))
    ok_restore = x1["digest"](s1) == x0["digest"](s0)
    x0["step"](s0, 500)
    x1["step"](s1, 500)
    ok_evolve = x1["digest"](s1) == x0["digest"](s0)
    after = file_sha(img)
    return {"restore_digest_matches": ok_restore, "evolution_digest_matches": ok_evolve,
            "image_matches_memory_sha": before == mem_sha,
            "image_unchanged_after_guest_writes": before == after}


def child(mode, img, pages, expect_digest):
    """Runs inside the probe cgroup: map or copy the image, then read it all."""
    engine = wasmtime.Engine()
    module = wasmtime.Module(engine, WAT.read_text())
    store, x = instance(engine, module)
    (map_image if mode == "map" else copy_image)(store, x, img, pages)
    seed, steps = expect_digest["globals"]
    x["seed"].set_value(store, seed)
    x["steps"].set_value(store, steps)
    d = x["digest"](store)                        # reads every byte of linear memory
    print(json.dumps({"digest_matches": d == expect_digest["digest"]}), flush=True)


def own_memcg():
    rel = next(line.split(":", 2)[2] for line in open("/proc/self/cgroup").read().splitlines()
               if line.split(":")[1] == "memory")
    return pathlib.Path("/sys/fs/cgroup/memory") / rel.lstrip("/")


def memstat(cg):
    out = {}
    for line in (cg / "memory.stat").read_text().splitlines():
        k, v = line.split()
        if k in ("cache", "rss", "mapped_file", "swap", "total_cache", "total_rss", "total_swap"):
            out[k] = int(v)
    return out


def in_cgroup(mode, img, pages, expect, limit, forbid_swap, tag):
    cg = own_memcg() / f"vre15-mapped-probe-{tag}-{os.getpid()}"
    cg.mkdir()
    try:
        (cg / "memory.limit_in_bytes").write_text(str(limit))
        if forbid_swap:
            (cg / "memory.memsw.limit_in_bytes").write_text(str(limit))
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable, "-I", str(pathlib.Path(__file__).resolve()), "--child", mode, str(img),
                            str(pages), json.dumps(expect)],
                           preexec_fn=lambda: (cg / "cgroup.procs").write_text(str(os.getpid())),
                           capture_output=True, text=True, timeout=600)
        oom = next(int(line.split()[1]) for line in (cg / "memory.oom_control").read_text().splitlines()
                   if line.startswith("oom_kill "))
        res = {"mode": mode, "limit_bytes": limit, "swap_forbidden": forbid_swap, "exit_code": p.returncode,
               "oom_kills": oom, "seconds": round(time.perf_counter() - t0, 2),
               "max_usage_bytes": int((cg / "memory.max_usage_in_bytes").read_text()),
               "failcnt": int((cg / "memory.failcnt").read_text()), "memory_stat": memstat(cg)}
        if p.stdout.strip():
            res.update(json.loads(p.stdout.strip().splitlines()[-1]))
        if p.returncode != 0:
            res["stderr_tail"] = p.stderr[-300:]
        return res
    finally:
        (cg / "memory.force_empty").write_text("0") if (cg / "memory.force_empty").exists() else None
        for _ in range(50):
            try:
                cg.rmdir()
                break
            except OSError:
                time.sleep(0.1)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], int(sys.argv[4]), json.loads(sys.argv[5]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("needs root (cgroups)", file=sys.stderr)
        return 2
    engine = wasmtime.Engine()
    module = wasmtime.Module(engine, WAT.read_text())
    work = pathlib.Path(tempfile.mkdtemp(prefix="vre15-mapped-"))
    report = {"kernel": os.uname().release, "wasmtime_py": __import__("importlib.metadata").metadata.version("wasmtime")}
    report["A_B_correctness_isolation"] = correctness(engine, module, work)
    # A 64 MiB tenant (1024 Wasm pages), 25% non-zero: 16 MiB of real state.
    s, x = instance(engine, module)
    x["fill"](s, 1024, 250, 7)
    x["step"](s, 1000)
    big = work / "big.img"
    write_image(s, x, big)
    expect = {"digest": x["digest"](s), "globals": [x["seed"].value(s), x["steps"].value(s)]}
    del s, x
    os.sync()
    pathlib.Path("/proc/sys/vm/drop_caches").write_text("3")
    # C: charging, with room to spare (256 MiB, swap forbidden).
    report["C_charging_unconstrained"] = in_cgroup("map", big, 1024, expect, 256 << 20, True, "c")
    # D: eviction through a 16 MiB limit with swap forbidden: mapped vs anonymous copy.
    pathlib.Path("/proc/sys/vm/drop_caches").write_text("3")
    report["D_mapped_16MiB_no_swap"] = in_cgroup("map", big, 1024, expect, 16 << 20, True, "dm")
    pathlib.Path("/proc/sys/vm/drop_caches").write_text("3")
    report["D_anon_copy_16MiB_no_swap"] = in_cgroup("copy", big, 1024, expect, 16 << 20, True, "da")
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    text = json.dumps(report, indent=1)
    print(text)
    if a.out:
        a.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
