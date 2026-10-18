"""Kernel substrate for VRE-0: one cgroup per execution, on cgroup v1 or v2.

Every value this module returns is read from the kernel (cgroupfs, /proc),
never computed from what the supervisor intended to write.

  v2 (unified): memory.max, memory.swap.max, memory.oom.group,
                memory.events oom_kill, memory.peak, cpu.stat usage_usec
  v1 (legacy):  memory.limit_in_bytes, memory.memsw.limit_in_bytes,
                memory.oom_control oom_kill, memory.failcnt,
                memory.max_usage_in_bytes, cpuacct.usage

Kernel caveat (do not drop it from any report): the memory limit is enforced
by reclaim and the OOM killer. Usage can sit at or briefly above the limit
before the kill lands, so this is a kernel-enforced boundary, not a
mathematical guarantee.
"""

from __future__ import annotations

import os
import pathlib
import platform


def _read(p: pathlib.Path) -> str:
    return p.read_text().strip()


def _kv(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _mounts() -> list[tuple[str, str, str]]:
    """(mount_point, fstype, super_options) from /proc/self/mountinfo."""
    rows = []
    for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fstype, _source, opts = right.split(" ", 2)
        rows.append((left.split()[4], fstype, opts))
    return rows


def _self_cgroups() -> dict[str, str]:
    """controller name ('' for unified) -> path, from /proc/self/cgroup."""
    out = {}
    for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines():
        _id, ctrls, path = line.split(":", 2)
        for c in (ctrls.split(",") if ctrls else [""]):
            out[c] = path
    return out


def proc_identity(pid: int) -> dict:
    """PID plus its start time in clock ticks: (boot_id, pid, starttime) names
    one process for the lifetime of the boot, unlike a bare PID."""
    stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rindex(")") + 2:].split()
    return {"pid": pid, "starttime_ticks": int(fields[19])}


def proc_cgroup_paths(pid: int) -> dict[str, str]:
    out = {}
    for line in pathlib.Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        _id, ctrls, path = line.split(":", 2)
        for c in (ctrls.split(",") if ctrls else [""]):
            out[c] = path
    return out


def host_facts() -> dict:
    mem = _kv("\n".join(line.replace(":", "").replace(" kB", "")
                        for line in pathlib.Path("/proc/meminfo").read_text().splitlines()))
    return {
        "kernel": platform.release(),
        "boot_id": _read(pathlib.Path("/proc/sys/kernel/random/boot_id")),
        "mem_total_kib": mem.get("MemTotal"),
        "swap_total_kib": mem.get("SwapTotal"),
        "nproc": os.cpu_count(),
    }


def mem_available_kib() -> int:
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise RuntimeError("MemAvailable missing from /proc/meminfo")


class Leaf:
    """One execution's cgroup(s). `dirs` maps hierarchy name -> directory."""

    def __init__(self, substrate: "Substrate", exec_id: str):
        self.sub = substrate
        self.exec_id = exec_id
        self.dirs = {h: base / exec_id for h, base in substrate.run_dirs.items()}

    # -- materialization -------------------------------------------------
    def create(self, memory_bytes: int) -> None:
        for d in self.dirs.values():
            d.mkdir()
        self.sub.configure(self, memory_bytes)

    def place_self(self) -> None:
        """Runs in the forked child before exec (subprocess preexec_fn)."""
        pid = str(os.getpid())
        for d in self.dirs.values():
            (d / "cgroup.procs").write_text(pid)

    # -- observation -------------------------------------------------------
    def identity(self) -> dict:
        return {
            "paths": {h: str(d) for h, d in self.dirs.items()},
            "relative_paths": {h: self.sub.relative(h, d) for h, d in self.dirs.items()},
            "inodes": {h: os.stat(d).st_ino for h, d in self.dirs.items()},
        }

    def readback(self) -> dict:
        return self.sub.readback(self)

    def counters(self) -> dict:
        return self.sub.counters(self)

    def membership(self, pid: int) -> dict:
        actual = proc_cgroup_paths(pid)
        out = {}
        for h, d in self.dirs.items():
            want = self.sub.relative(h, d)
            got = actual.get(self.sub.proc_key[h])
            out[h] = {"expected": want, "observed": got, "match": got == want}
        return out

    def procs(self) -> list[int]:
        seen = set()
        for d in self.dirs.values():
            seen.update(int(x) for x in _read(d / "cgroup.procs").split())
        return sorted(seen)

    def destroy(self) -> dict:
        result = {}
        for h, d in self.dirs.items():
            try:
                d.rmdir()
                result[h] = "removed"
            except OSError as e:
                result[h] = f"error:{e.strerror}"
        return result


class Substrate:
    version: int
    run_dirs: dict[str, pathlib.Path]
    proc_key: dict[str, str]
    mounts: dict[str, pathlib.Path]

    @staticmethod
    def detect(run_id: str) -> "Substrate":
        cgs = _self_cgroups()
        if "memory" in cgs:
            return CgroupV1(run_id)
        if set(cgs) == {""}:
            return CgroupV2(run_id)
        raise RuntimeError(f"no usable memory controller: /proc/self/cgroup={cgs}")

    def relative(self, hierarchy: str, d: pathlib.Path) -> str:
        return "/" + str(d.relative_to(self.mounts[hierarchy])).lstrip(".").lstrip("/")

    def leaf(self, exec_id: str) -> Leaf:
        return Leaf(self, exec_id)

    def describe(self) -> dict:
        return {"cgroup_version": self.version,
                "run_dirs": {h: str(d) for h, d in self.run_dirs.items()}}

    def teardown(self) -> None:
        for d in self.run_dirs.values():
            try:
                d.rmdir()
            except OSError:
                pass


class CgroupV1(Substrate):
    version = 1

    def __init__(self, run_id: str):
        cgs = _self_cgroups()
        self.mounts, self.proc_key, self.run_dirs = {}, {}, {}
        for h in ("memory", "cpuacct"):
            mount = next((pathlib.Path(mp) for mp, fs, opts in _mounts()
                          if fs == "cgroup" and h in opts.split(",")), None)
            if mount is None or h not in cgs:
                raise RuntimeError(f"cgroup v1 {h} hierarchy not mounted")
            own = mount / cgs[h].lstrip("/")
            run_dir = own / f"vre0-{run_id}"
            run_dir.mkdir()
            self.mounts[h], self.proc_key[h], self.run_dirs[h] = mount, h, run_dir
        self.supervisor_memcg = self.mounts["memory"] / cgs["memory"].lstrip("/")

    def configure(self, leaf: Leaf, memory_bytes: int) -> None:
        m = leaf.dirs["memory"]
        (m / "memory.limit_in_bytes").write_text(str(memory_bytes))
        # Without this, v1 lets the group spill into swap beyond the limit.
        if (m / "memory.memsw.limit_in_bytes").exists():
            (m / "memory.memsw.limit_in_bytes").write_text(str(memory_bytes))

    def readback(self, leaf: Leaf) -> dict:
        m = leaf.dirs["memory"]
        sw = m / "memory.memsw.limit_in_bytes"
        return {
            "memory_limit_bytes": int(_read(m / "memory.limit_in_bytes")),
            "memory_plus_swap_limit_bytes": int(_read(sw)) if sw.exists() else None,
            "oom_kill_disable": _kv(_read(m / "memory.oom_control")).get("oom_kill_disable"),
        }

    def counters(self, leaf: Leaf) -> dict:
        m = leaf.dirs["memory"]
        memsw = m / "memory.memsw.failcnt"
        return {
            "oom_kill": _kv(_read(m / "memory.oom_control")).get("oom_kill"),
            "limit_hits": int(_read(m / "memory.failcnt")),
            # Both fail counters are recorded as the kernel reports them. On
            # the reference host (6.18, v1) both read 0 even across OOM kills,
            # so nothing classifies on them.
            "swap_limit_hits": int(_read(memsw)) if memsw.exists() else None,
            "usage_bytes": int(_read(m / "memory.usage_in_bytes")),
            "peak_bytes": int(_read(m / "memory.max_usage_in_bytes")),
            "cpu_ns": int(_read(leaf.dirs["cpuacct"] / "cpuacct.usage")),
        }

    def supervisor_oom_kill(self) -> int:
        return _kv(_read(self.supervisor_memcg / "memory.oom_control")).get("oom_kill", 0)


class CgroupV2(Substrate):
    """Not exercised on the host that produced the committed run (that host
    binds the memory controller to v1). Its own readback and membership checks
    refuse to release a workload rather than pass one on a bad binding."""

    version = 2

    def __init__(self, run_id: str):
        mount = next((pathlib.Path(mp) for mp, fs, _ in _mounts() if fs == "cgroup2"), None)
        if mount is None:
            raise RuntimeError("cgroup2 not mounted")
        own = mount / _self_cgroups()[""].lstrip("/")
        if "memory" not in _read(own / "cgroup.controllers").split():
            raise RuntimeError(f"memory controller not delegated to {own}")
        # No-internal-process rule: move the supervisor to a leaf before
        # enabling controllers for children of its cgroup.
        sup = own / "vre0-supervisor"
        sup.mkdir(exist_ok=True)
        (sup / "cgroup.procs").write_text(str(os.getpid()))
        (own / "cgroup.subtree_control").write_text("+memory")
        run_dir = own / f"vre0-{run_id}"
        run_dir.mkdir()
        (run_dir / "cgroup.subtree_control").write_text("+memory")
        self.mounts = {"unified": mount}
        self.proc_key = {"unified": ""}
        self.run_dirs = {"unified": run_dir}
        self.supervisor_memcg = sup

    def configure(self, leaf: Leaf, memory_bytes: int) -> None:
        d = leaf.dirs["unified"]
        (d / "memory.max").write_text(str(memory_bytes))
        if (d / "memory.swap.max").exists():
            (d / "memory.swap.max").write_text("0")
        (d / "memory.oom.group").write_text("1")

    def readback(self, leaf: Leaf) -> dict:
        d = leaf.dirs["unified"]
        sw = d / "memory.swap.max"
        return {
            "memory_limit_bytes": int(_read(d / "memory.max")),
            "swap_limit_bytes": int(_read(sw)) if sw.exists() else None,
            "oom_group": int(_read(d / "memory.oom.group")),
        }

    def counters(self, leaf: Leaf) -> dict:
        d = leaf.dirs["unified"]
        ev = _kv(_read(d / "memory.events"))
        peak = d / "memory.peak"
        return {
            "oom_kill": ev.get("oom_kill"),
            "limit_hits": ev.get("max"),
            "swap_limit_hits": None,
            "usage_bytes": int(_read(d / "memory.current")),
            "peak_bytes": int(_read(peak)) if peak.exists() else None,
            "cpu_ns": _kv(_read(d / "cpu.stat")).get("usage_usec", 0) * 1000,
        }

    def supervisor_oom_kill(self) -> int:
        return _kv(_read(self.supervisor_memcg / "memory.events")).get("oom_kill", 0)
