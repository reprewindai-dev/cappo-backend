# LINUX-BINDING-0 — SEALED LOCAL PROOF

**Sealed:** 2026-09-10  
**Status:** VALID  
**Scope:** Local WSL2 substrate, elevated/root execution

---

## Result

```
LINUX-BINDING-0: VALID
All 5 core invariants: VALID
All 3 hostile checks: VALID
```

---

## Provenance

| Field | Value |
|---|---|
| session_id | veklom_lb0_1789011932 |
| boot_id | 6792a973-dd95-46a1-af29-ed14796a6e46 |
| kernel | 6.6.87.2-microsoft-standard-WSL2 |
| Python | 3.14.4 |
| Distribution | Ubuntu 26.04 LTS |
| Architecture | x86_64 |
| Privilege | root (elevated) |
| Execution timestamp | 2026-09-10T03:45:33Z |
| probe_sha256 | d834deb0e6f44eb5b5a813b6181c71c32be5cdb605c12902a90a38d4cc95c09e |
| result_sha256 | 2b4352274f3d756b9b236000738071948a022e4ebbbed25e0d335640a597a2ce |

---

## What Was Proven

**B0-L1 — Execution identity tuple captured and joined**  
`boot_id=6792a973... + pidns(st_dev=4, st_ino=4026532219) + PID + starttime_jiffies`  
Tuple is globally unique within this boot and namespace. Namespace scoping is explicit — the same numeric PID can exist in a different PID namespace with a different `(st_dev, st_ino)`.

**B0-L2 — pidfd remains bound to original process lifetime**  
After child exit: pidfd became pollable (`POLLIN` confirmed); `pidfd_send_signal` returned `ESRCH`. The fd itself remained valid until explicitly closed. `EBADF` was not used as the exit signal — process death is detected via `ESRCH`/poll, not fd invalidation.

**B0-L3 — cgroup membership independently read back**  
Process assigned to `/veklom_b0_test`. `/proc/pid/cgroup` confirmed membership.  
cgroup object identity captured as `(st_dev=23, st_ino=2688)` — inode, not path.  
Both `cgroup_path` and `cgroup_object_identity` preserved: path for provenance, inode for security property.

**B0-L4 — Same-name cgroup recreation does not inherit observed binding state**  
`original_inode=2688` → destroyed → recreated → `new_inode=2732`.  
New `cgroup.procs` empty. No process membership inherited.  
Security property: a pathname alone is insufficient to inherit a previous binding. A recreated cgroup must be freshly rebound and its current kernel identity revalidated.  
*Note: the proof demonstrates that inode changed on this run. This does not claim that numeric cgroup inodes can never be reused across kernel lifetime — the security property is pathname insufficiency, not inode eternal uniqueness.*

**B0-L5 — Namespace identity distinguished by (st_dev, st_ino)**  
pidns: `(4, 4026532219)` matched self. netns: `(4, 4026531840)` matched self.  
`unshare -n` child received `netns st_ino=4026532225` — different kernel object confirmed.

**H3 — Namespace mismatch detected**  
`unshare -n` child: `st_ino=4026532225` vs parent: `st_ino=4026531840`. Divergence confirmed by `(st_dev, st_ino)` pair — not inode alone.

**H4 — Cgroup reassignment invalidated recorded membership**  
Process placed into `/veklom_b0_test`, then moved to `/`. `/proc/pid/cgroup` changed.  
Recorded cgroup path no longer matched — binding correctly detected as invalid.

**H5 — Stale pidfd rejected after exit; starttime collision observed across distinct PIDs**  
`child_a` (PID=505, starttime=1068) killed. `child_b` (PID=506, starttime=1068) spawned immediately.  
Two **distinct** processes (different PIDs) received the **same starttime jiffies value** — demonstrating that `starttime` alone is insufficient for unique process identity at jiffy granularity.  
After `child_a` exit, `pidfd_a` returned `ESRCH` and could not address `child_b`.  
This does not claim PID reuse was reproduced (A and B had different PIDs). The pidfd instance-binding property is directly exercised: the fd is bound to the specific process instance, not to the numeric PID or starttime value.

---

## Not Claimed

| Claim | Status |
|---|---|
| Literal numeric PID reuse reproduced | NOT EXECUTED — see H6 below |
| Global uniqueness of starttime across all possible jiffies values | NOT CLAIMED |
| Permanent non-reuse of numeric cgroup inode across kernel lifetime | NOT CLAIMED |
| Portability beyond WSL2/6.6.87/Ubuntu 26.04/x86_64 | NOT CLAIMED |

**H6 — Actual PID reuse stress test**  
Status: `NOT EXECUTED / INDETERMINATE`  
Goal: Force reuse of numeric PID P by rapidly spawning/killing processes, then prove that a stale pidfd for the original P returns `ESRCH` when the replacement process holds PID P.  
Not required to seal LINUX-BINDING-0. The pidfd instance-binding property is already directly demonstrated via post-exit `ESRCH` in H5 and B0-L2.

---

## Privilege Scope

| Probe | Requires elevation |
|---|---|
| B0-L1, B0-L2, B0-L5, H5 | No — runs unprivileged |
| B0-L3, B0-L4, H3, H4 | Yes — requires cgroup write access and `unshare` capability (root or `CAP_SYS_ADMIN`) |

---

## Program State After This Seal

```
LINUX-SPEC-MAP-0    ✅ SEALED
LINUX-BINDING-0     ✅ SEALED / VALID
LINUX-VRE-1         🟢 GO
LINUX-EVIDENCE-1    ⏸ HOLD
```
