"""vre15/cgroups.py: one memory-cgroup contract on cgroup v1 and v2.

The v2 file logic is checked against a fake cgroup tree (any machine). The
real contract, budget enforced, OOM kills counted, swap allowed or not, is
checked by the selftest on the running kernel when the tests run as root.
"""

import os
import sys

import pytest

from vre15 import cgroups


@pytest.fixture
def fake_v2(tmp_path):
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory hugetlb pids rdma\n")
    (tmp_path / "cgroup.subtree_control").write_text("cpu memory\n")
    (tmp_path / "vre15").mkdir()
    (tmp_path / "vre15" / "cgroup.subtree_control").write_text("\n")
    return tmp_path


def test_version_detection(fake_v2, tmp_path_factory):
    assert cgroups.version(fake_v2) == "v2"
    assert cgroups.version(tmp_path_factory.mktemp("v1root")) == "v1"


def test_v2_budget_and_swap_policy_are_written(fake_v2):
    managed = cgroups.MemoryCgroup.create("managed", 16 << 20, allow_swap=False, root=fake_v2)
    paged = cgroups.MemoryCgroup.create("paged", 16 << 20, allow_swap=True, root=fake_v2)
    assert managed.path == fake_v2 / "vre15" / "managed" and managed.version == "v2"
    assert (managed.path / "memory.max").read_text() == str(16 << 20)
    assert (managed.path / "memory.swap.max").read_text() == "0"         # anonymous memory cannot hide in swap
    assert (paged.path / "memory.swap.max").read_text() == "max"
    assert (fake_v2 / "vre15" / "cgroup.subtree_control").read_text() == "+memory"  # delegated to children


def test_v2_counters_are_read_from_memory_events(fake_v2):
    cg = cgroups.MemoryCgroup.create("c", 1 << 20, allow_swap=False, root=fake_v2)
    (cg.path / "memory.events").write_text("low 0\nhigh 0\nmax 7\noom 2\noom_kill 1\noom_group_kill 0\n")
    (cg.path / "memory.current").write_text("1040384\n")
    (cg.path / "memory.swap.current").write_text("0\n")
    assert (cg.oom_kills(), cg.limit_hits(), cg.usage(), cg.swap_bytes()) == (1, 7, 1040384, 0)
    assert cg.usage_file().name == "memory.current"


def test_v2_without_memory_controller_is_refused(tmp_path):
    (tmp_path / "cgroup.controllers").write_text("cpu io pids\n")
    (tmp_path / "cgroup.subtree_control").write_text("\n")
    with pytest.raises(RuntimeError):
        cgroups.MemoryCgroup.create("x", 1 << 20, allow_swap=False, root=tmp_path)


def test_v1_counters_are_read_from_oom_control_and_failcnt(tmp_path):
    cg = cgroups.MemoryCgroup(tmp_path, "v1")
    (tmp_path / "memory.oom_control").write_text("oom_kill_disable 0\nunder_oom 0\noom_kill 3\n")
    (tmp_path / "memory.failcnt").write_text("12\n")
    (tmp_path / "memory.usage_in_bytes").write_text("4096\n")
    assert (cg.oom_kills(), cg.limit_hits(), cg.usage()) == (3, 12, 4096)


@pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() != 0,
                    reason="the real-kernel selftest needs root (it creates cgroups)")
def test_selftest_on_this_kernel():
    r = cgroups.selftest()
    assert r["ok"], r
    assert r["checks"]["over_budget_without_swap_is_killed"]["oom_kills"] >= 1
    # The transfer run's form: all three checks must run, with a temporary
    # swap file when none is active, and the swap file must be gone after.
    before = open("/proc/swaps").read()
    r = cgroups.selftest(temp_swap=True)
    assert r["ok"] and set(cgroups.REQUIRED_CHECKS) <= set(r["checks"]), r
    assert open("/proc/swaps").read() == before
