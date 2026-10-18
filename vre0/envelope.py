"""
VRE-0 Envelope Schema
=====================
Defines the signed execution envelope and TransitionReceipt data contracts.

The envelope is the authority document. The receipt proves what happened.
Both are JSON-serializable; the digest is SHA-256 over canonical JSON.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class OutcomeCode(str, Enum):
    COMPLETED = "COMPLETED"
    ENVELOPE_MEMORY_EXCEEDED = "ENVELOPE_MEMORY_EXCEEDED"
    ENVELOPE_COMPUTE_EXHAUSTED = "ENVELOPE_COMPUTE_EXHAUSTED"
    ENVELOPE_DEADLINE_EXCEEDED = "ENVELOPE_DEADLINE_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class ExecutionEnvelope:
    """
    Signed execution contract. All fields are declared before materialization.
    No field in any downstream step may expand what this contract permits.
    """

    # Identity
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: float = field(default_factory=time.time)

    # Scenario label (for tracing)
    scenario: str = "unknown"

    # Resource bounds (all are HARD limits — not targets or preferences)
    memory_max_bytes: int = 64 * 1024 * 1024   # 64 MiB hard cgroup boundary
    fuel_budget: int = 50_000_000               # wasmtime fuel units
    wall_deadline_seconds: float = 5.0          # outer supervisor wall-clock kill

    # Evidence level commitment
    evidence_level: str = "E2"  # E2 = substrate-observed, host-trust-domain

    # Authority metadata (placeholder for full Biscuit/CAPPO integration)
    biscuit_token: str = ""

    def canonical_json(self) -> str:
        """Stable canonical JSON for digest computation."""
        d = {
            "envelope_id": self.envelope_id,
            "issued_at": self.issued_at,
            "scenario": self.scenario,
            "memory_max_bytes": self.memory_max_bytes,
            "fuel_budget": self.fuel_budget,
            "wall_deadline_seconds": self.wall_deadline_seconds,
            "evidence_level": self.evidence_level,
            "biscuit_token": self.biscuit_token,
        }
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        """SHA-256 of canonical JSON — binds the signed contract to execution."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass
class SubstrateEvidence:
    """
    Evidence collected from substrate-observed sources (cgroup, OS signals).
    This is NOT from the workload's self-report — it is read by the supervisor
    directly from /sys/fs/cgroup and process exit codes.
    """

    # Identity fields (for VRE-0B binding)
    execution_uuid: str = ""
    host_pid: Optional[int] = None
    pid_tree: list[int] = field(default_factory=list)
    cgroup_path: str = ""

    # Configured limits (read back from cgroup at materialization)
    configured_memory_max_bytes: Optional[int] = None
    configured_fuel_budget: Optional[int] = None

    # Observed runtime state (VRE-0A containment + VRE-0C evidence chain)
    peak_memory_bytes: Optional[int] = None
    memory_max_at_termination: Optional[int] = None
    exit_code: Optional[int] = None
    exit_signal: Optional[str] = None
    oom_killed: bool = False
    fuel_trap_reason: Optional[str] = None
    deadline_exceeded: bool = False

    # Timestamps
    materialization_ts: Optional[float] = None
    termination_ts: Optional[float] = None

    def elapsed_seconds(self) -> Optional[float]:
        if self.materialization_ts and self.termination_ts:
            return self.termination_ts - self.materialization_ts
        return None


@dataclass
class TransitionReceipt:
    """
    Cryptographic linkage from Envelope → Boundary → Execution → Outcome.

    VRE-0C requirement: content must be reconstructable from substrate_evidence
    independent of any workload self-report.
    """

    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    envelope_digest: str = ""
    execution_uuid: str = ""
    scenario: str = ""

    # Binding proof (VRE-0B)
    cgroup_path: str = ""
    host_pid: Optional[int] = None
    configured_memory_max_bytes: Optional[int] = None
    configured_fuel_budget: Optional[int] = None
    envelope_memory_max_bytes: Optional[int] = None
    envelope_fuel_budget: Optional[int] = None
    binding_verified: bool = False
    binding_failure_reason: Optional[str] = None

    # Containment proof (VRE-0A)
    host_survived: bool = False
    workload_contained: bool = False

    # Outcome
    outcome: OutcomeCode = OutcomeCode.INTERNAL_ERROR
    outcome_evidence_source: str = ""  # which substrate field drove the outcome

    # Full evidence (VRE-0C)
    evidence: Optional[SubstrateEvidence] = None

    # Timestamps
    issued_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        if self.evidence:
            d["evidence"] = asdict(self.evidence)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
