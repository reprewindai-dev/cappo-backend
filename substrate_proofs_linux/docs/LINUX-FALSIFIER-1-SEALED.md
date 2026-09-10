# LINUX-FALSIFIER-1 Post-Authorization Execution Substitution

**Status**: VALID
**Environment**: WSL2 Linux 6.6.87, root
**Test Date**: 2026-09-10
**Code Path**: `probes/linux_falsifier_1/`

## Objective
To prove that an execution authorized and bound at time T1 cannot be replaced by a different Linux process/cgroup at T2 (even if completely identical in pathname, executable, and arguments) and successfully inherit the original authority to produce the target consequence. 

This establishes defense against the TOCTOU (Time-Of-Check to Time-Of-Use) Confused Binding Attack.

## Methodology
1. **Execution A**: Created cgroup `cappo_falsifier_test`, generated immutable kernel identities (inode `3991`, pidfd, starttime). Issued authority token for A.
2. **Execution Substitution**: The adversary killed Execution A, tore down the cgroup, and recreated it at the exact same human-readable path (`/sys/fs/cgroup/cappo_falsifier_test`) running the exact same executable, attempting to hijack the dispatch capability.
3. **Dispatch Evaluation (A's Authority on B's Execution)**:
   - The executor attempted to use A's token.
   - The dispatcher strictly evaluated the *current* state of the target path and PID against the bound token.
   - **Result**: The dispatcher natively rejected the dispatch (`DENY`) because the `pidfd` registered `ESRCH` (Execution A dead) and the cgroup inode identity had advanced to `4035`.
4. **Legitimate Execution (B's Authority on B's Execution)**:
   - Generated a fresh, correct authority token for B.
   - **Result**: Dispatcher validated identities (`4035 == 4035`, pidfd `OK`). Granted `ALLOW`.

## Finality & Evidence Packet
```json
{
  "milestone": "LINUX-FALSIFIER-1",
  "verdict": "VALID",
  "lease_identifier": "lease-b7b1a131",
  "evidence_packet": {
    "A_binding_digest": "fcb6686b91763daf2f80a96312d154da0af67606c358459bcf0ba51ecc9a2fe6",
    "A_pidfd_termination_result": "ESRCH (Process terminated)",
    "old_cgroup_identity": 3991,
    "new_cgroup_identity": 4035,
    "B_binding_digest": "3ddaea0998a20878231c9336183518883bfd421684ce7d1a643c1e9d3cc309a1",
    "rejected_dispatch": {
      "binding_comparison_result": "pidfd: ESRCH (Process terminated), cg_id: 4035 != 3991",
      "dispatch_decision": "DENY"
    },
    "legitimate_dispatch": {
      "binding_comparison_result": "pidfd: OK, cg_id: 4035 == 4035",
      "dispatch_decision": "ALLOW"
    },
    "consequences": {
      "target_state_before": 0,
      "substituted_execution_consequences": 0,
      "legitimate_execution_consequences": 1
    }
  }
}
```

## Conclusion
The composition of Linux immutable kernel identities (`cgroup_id`, `pidfd`, `starttime`) perfectly defends against post-authorization substitution. Re-creating the execution envelope natively advances the kernel object IDs, ensuring that any attempt to re-use an old authority over a new execution results in an undeniable `DENY` and exactly zero target consequences. 

The Linux local proof phase is closed.
