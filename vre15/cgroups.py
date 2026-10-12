"""Memory cgroups for VRE-1.5 runs, on cgroup v1 or cgroup v2.

    sudo python -m vre15.cgroups --selftest

One interface, two kernels:

                     v1 (this repo's cloud host)          v2 (WSL2, current distributions)
  where              own memcg in /sys/fs/cgroup/memory    /sys/fs/cgroup/vre15/<name>
  budget             memory.limit_in_bytes                 memory.max
  forbid swap        memory.memsw.limit_in_bytes = budget  memory.swap.max = 0
  allow swap         (memsw left unlimited)                memory.swap.max = max
  charge             memory.usage_in_bytes                 memory.current
  hit the limit      memory.failcnt                        memory.events "max"
  OOM kills          memory.oom_control "oom_kill"         memory.events "oom_kill"
  join               cgroup.procs                          cgroup.procs

The two are not identical kernels. What is equal is the contract: the
budget caps the group's charge, forbidding swap leaves anonymous memory
nowhere to go but OOM, and kills are counted. The selftest checks exactly
that contract on the running kernel before an experiment relies on it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path("/sys/fs/cgroup")


def version(root: pathlib.Path = ROOT) -> str:
    return "v2" if (root / "cgroup.controllers").exists() else "v1"


def _read_kv(path: pathlib.Path) -> dict[str, int]:
    out = {}
    for line in path.read_text().splitlines():
        k, _, v = line.partition(" ")
        if v.strip().lstrip("-").isdigit():
            out[k] = int(v)
    return out


def _read_int(path: pathlib.Path):
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


class MemoryCgroup:
    """A memory cgroup with a budget; `allow_swap` decides whether anonymous
    memory over the budget may go to swap."""

    def __init__(self, path: pathlib.Path, ver: str) -> None:
        self.path, self.version = pathlib.Path(path), ver

    # -- creation --------------------------------------------------------------
    @classmethod
    def create(cls, name: str, budget: int, allow_swap: bool, root: pathlib.Path = ROOT) -> "MemoryCgroup":
        ver = version(root)
        if ver == "v1":
            base = own_v1_memcg(root)
            cg = base / name
            cg.mkdir()
            (cg / "memory.limit_in_bytes").write_text(str(budget))
            if not allow_swap:
                (cg / "memory.memsw.limit_in_bytes").write_text(str(budget))
        else:
            parent = v2_parent(root)
            cg = parent / name
            cg.mkdir()
            (cg / "memory.max").write_text(str(budget))
            (cg / "memory.swap.max").write_text("max" if allow_swap else "0")
        return cls(cg, ver)

    # -- use ---------------------------------------------------------------------
    @property
    def procs(self) -> pathlib.Path:
        return self.path / "cgroup.procs"

    def join(self, pid: int | None = None) -> None:
        self.procs.write_text(str(pid if pid is not None else os.getpid()))

    def usage_file(self) -> pathlib.Path:
        return self.path / ("memory.usage_in_bytes" if self.version == "v1" else "memory.current")

    def usage(self):
        return _read_int(self.usage_file())

    def limit_hits(self):
        if self.version == "v1":
            return _read_int(self.path / "memory.failcnt")
        return _read_kv(self.path / "memory.events").get("max")

    def oom_kills(self) -> int:
        f = "memory.oom_control" if self.version == "v1" else "memory.events"
        return _read_kv(self.path / f).get("oom_kill", 0)

    def swap_bytes(self):
        if self.version == "v1":
            mem, memsw = self.usage(), _read_int(self.path / "memory.memsw.usage_in_bytes")
            return None if mem is None or memsw is None else memsw - mem
        return _read_int(self.path / "memory.swap.current")

    def remove(self) -> None:
        for _ in range(50):
            try:
                self.path.rmdir()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.1)
        self.path.rmdir()


def own_v1_memcg(root: pathlib.Path = ROOT) -> pathlib.Path:
    rel = next(line.split(":", 2)[2] for line in open("/proc/self/cgroup").read().splitlines()
               if "memory" in line.split(":")[1].split(","))
    return root / "memory" / rel.lstrip("/")


def v2_parent(root: pathlib.Path = ROOT) -> pathlib.Path:
    """/sys/fs/cgroup/vre15, with the memory controller delegated to its
    children. On v2 a cgroup that holds processes cannot also have children
    with controllers, so experiment groups live under this empty parent, not
    under the caller's own cgroup."""
    if "memory" not in (root / "cgroup.controllers").read_text().split():
        raise RuntimeError("cgroup v2 without the memory controller")
    if "memory" not in (root / "cgroup.subtree_control").read_text().split():
        (root / "cgroup.subtree_control").write_text("+memory")
    parent = root / "vre15"
    parent.mkdir(exist_ok=True)
    if "memory" not in (parent / "cgroup.subtree_control").read_text().split():
        (parent / "cgroup.subtree_control").write_text("+memory")
    return parent


def path_of_pid(pid: int | str = "self", root: pathlib.Path = ROOT) -> pathlib.Path | None:
    """The memory cgroup directory a process is in, on either version."""
    lines = open(f"/proc/{pid}/cgroup").read().splitlines()
    for line in lines:
        hid, ctrls, rel = line.split(":", 2)
        if "memory" in ctrls.split(","):
            return root / "memory" / rel.lstrip("/")
    for line in lines:
        hid, ctrls, rel = line.split(":", 2)
        if hid == "0" and ctrls == "":
            return root / rel.lstrip("/")
    return None


def usage_path_of_self(root: pathlib.Path = ROOT) -> str | None:
    p = path_of_pid("self", root)
    if p is None:
        return None
    return str(p / ("memory.current" if (p / "memory.current").exists() else "memory.usage_in_bytes"))


# -- selftest ------------------------------------------------------------------
_TOUCH = """
import mmap, sys
n = int(sys.argv[1]) << 20
m = mmap.mmap(-1, n, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
for off in range(0, n, 4096):
    m[off] = 1
print("ok", flush=True)
"""


def _child(cg: MemoryCgroup, mib: int) -> int:
    p = subprocess.run([sys.executable, "-I", "-c", _TOUCH, str(mib)], preexec_fn=cg.join,
                       capture_output=True, text=True, timeout=120)
    return p.returncode


def selftest() -> dict:
    """Check the contract the experiments rely on, on this kernel."""
    swaps = [line.split()[0] for line in open("/proc/swaps").read().splitlines()[1:]]
    report = {"cgroup": version(), "kernel": os.uname().release, "swap_devices": swaps,
              "mglru": (open("/sys/kernel/mm/lru_gen/enabled").read().strip()
                        if os.path.exists("/sys/kernel/mm/lru_gen/enabled") else None),
              "checks": {}}
    tag = f"selftest-{os.getpid()}"
    budget = 32 << 20
    cases = [("fits_without_swap", 8, False, True), ("over_budget_without_swap_is_killed", 96, False, False)]
    if swaps:
        cases.append(("over_budget_with_swap_survives", 96, True, True))
    for name, mib, allow_swap, should_live in cases:
        cg = MemoryCgroup.create(f"{tag}-{name}", budget, allow_swap)
        try:
            rc = _child(cg, mib)
            kills = cg.oom_kills()
            ok = (rc == 0 and kills == 0) if should_live else (rc != 0 and kills >= 1)
            report["checks"][name] = {"ok": ok, "exit": rc, "oom_kills": kills,
                                      "limit_hits": cg.limit_hits(), "allocated_mib": mib,
                                      "budget_mib": budget >> 20, "swap_allowed": allow_swap}
        finally:
            cg.remove()
    report["ok"] = all(c["ok"] for c in report["checks"].values())
    return report


def main(argv) -> int:
    if argv[1:] != ["--selftest"]:
        print("usage: python -m vre15.cgroups --selftest", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("needs root (creates cgroups)", file=sys.stderr)
        return 2
    r = selftest()
    print(json.dumps(r, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
