"""
P5 Implementation-to-Model Correspondence Layer
================================================

This module defines:

  1. TruthTransitionTrace  -- canonical trace record for every truth-state mutation
  2. PYTHON_TO_TLA          -- the bijection from Python site/transition to TLA+ action
  3. KNOWN_MUTATION_SITES   -- exhaustive enumeration of service.py code sites that
                               can write ConsequenceExecutionEvent rows
  4. verify_trace()         -- asserts one trace step maps to a legal TLA+ action
  5. verify_mutation_surface() -- AST-scans service.py to confirm no unregistered
                                  ConsequenceExecutionEvent write site exists
  6. verify_no_model_gap()  -- confirms every TLA+ system action has a Python binding

Correspondence map (Python runtime  ->  TLA+ model):

  cappo_evaluator          "none"     -> "authorized"           : Authorize
  begin_consequence        "authorized" -> "started"            : Start
  completion_reporter      "started" -> "succeeded"             : CompleteSucceeded
  completion_reporter      "started" -> "failed"               : CompleteFailed
  completion_reporter      "started" -> "outcome_unknown"       : EnterUnknown
  completion_reporter      "outcome_unknown" -> "outcome_unknown"      : EnterUnknown (re-fence)
  completion_reporter      "outcome_unknown" -> "reconciled_succeeded" : ReconcileSucceeded
  completion_reporter      "outcome_unknown" -> "reconciled_failed"    : ReconcileFailed
  direct_orm_bypass        any                                  : ForgeState (hostile, must be blocked)
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import os
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical trace record
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TruthTransitionTrace:
    operation_id: str
    event_index: int                    # version column of the new event
    previous_truth_state: str           # state of the preceding event ("none" for first)
    next_truth_state: str               # state of this event
    intent_hash: str
    consequence_identity: str           # receipt_id bound to the operation
    proof_type: str                     # completion_proof_type
    proof_subject_hash: str
    actor_class: str                    # which Python site wrote this event
    tla_action: Optional[str] = None   # filled in by verify_trace()


# ---------------------------------------------------------------------------
# Python code site -> TLA+ action mapping
# ---------------------------------------------------------------------------
# Key: (previous_truth_state, next_truth_state, actor_class)
# All state strings are lowercase (matching ConsequenceState enum values).

PYTHON_TO_TLA: dict[tuple[str, str, str], str] = {
    # --- Normal system transitions ---
    ("none",            "authorized",           "cappo_evaluator")     : "Authorize",
    ("authorized",      "started",              "begin_consequence")   : "Start",
    ("started",         "succeeded",            "completion_reporter") : "CompleteSucceeded",
    ("started",         "failed",               "completion_reporter") : "CompleteFailed",
    ("started",         "outcome_unknown",      "completion_reporter") : "EnterUnknown",
    # Re-fencing an already-unknown state (outcome_uncertain re-report)
    ("outcome_unknown", "outcome_unknown",      "completion_reporter") : "EnterUnknown",
    # Reconciliation transitions
    ("outcome_unknown", "reconciled_succeeded", "completion_reporter") : "ReconcileSucceeded",
    ("outcome_unknown", "reconciled_failed",    "completion_reporter") : "ReconcileFailed",
    # --- Hostile / attacker-class transitions (must always be BLOCKED) ---
    ("started",         "succeeded",            "direct_orm_bypass")   : "ForgeState",
    ("authorized",      "succeeded",            "direct_orm_bypass")   : "ForgeState",
    ("outcome_unknown", "succeeded",            "direct_orm_bypass")   : "ForgeState",
    ("outcome_unknown", "reconciled_succeeded", "direct_orm_bypass")   : "ForgeState",
}

# ---------------------------------------------------------------------------
# Exhaustive mutation site registry
# ---------------------------------------------------------------------------
# Every code path in service.py capable of writing a ConsequenceExecutionEvent row.
# This set is a contract: if a new write site is added to service.py, it MUST be
# registered here AND a PYTHON_TO_TLA mapping must be added.

KNOWN_MUTATION_SITES: set[str] = {
    "cappo_evaluator",    # service.py L183-L197: writes AUTHORIZED event
    "begin_consequence",  # service.py L238-L253: writes STARTED event
    "completion_reporter",# service.py L356-L372: writes terminal events
    "direct_orm_bypass",  # test-only hostile path: direct db.add() bypassing engine
}

# TLA+ system actions (non-attacker) that must each have a Python binding
_TLA_SYSTEM_ACTIONS: set[str] = {
    "Authorize",
    "Start",
    "CompleteSucceeded",
    "CompleteFailed",
    "EnterUnknown",
    "ReconcileSucceeded",
    "ReconcileFailed",
}

_HOSTILE_TLA_ACTIONS: set[str] = {
    "ForgeState", "ReplayProof", "BypassAuthority",
    "SwapIntent", "SwapConsequence", "RollbackEpoch",
    "RaceReconcilers", "RaceExecutors", "SwapOperation",
    "ResolveUnknownWithoutProof",
}


# ---------------------------------------------------------------------------
# Verifier: single trace step
# ---------------------------------------------------------------------------

class CorrespondenceViolation(AssertionError):
    pass


def verify_trace(trace: TruthTransitionTrace) -> str:
    """
    Verify one trace step is governed by a known TLA+ action.
    Returns the TLA+ action name on success.
    Raises CorrespondenceViolation if:
      - actor_class not in KNOWN_MUTATION_SITES (unregistered write path)
      - no (prev, next, actor) mapping exists (unmodeled transition)
      - the mapping resolves to a hostile action that should have been blocked
    """
    if trace.actor_class not in KNOWN_MUTATION_SITES:
        raise CorrespondenceViolation(
            f"Unregistered mutation site {trace.actor_class!r} -- not in KNOWN_MUTATION_SITES. "
            f"Either register it in the correspondence map or confirm it is an unauthorized bypass."
        )

    key = (trace.previous_truth_state, trace.next_truth_state, trace.actor_class)
    tla_action = PYTHON_TO_TLA.get(key)

    if tla_action is None:
        raise CorrespondenceViolation(
            f"No TLA+ mapping for Python transition: "
            f"prev={trace.previous_truth_state!r} next={trace.next_truth_state!r} "
            f"actor={trace.actor_class!r}. "
            f"This transition is unmodeled -- extend PYTHON_TO_TLA or block it."
        )

    if tla_action in _HOSTILE_TLA_ACTIONS:
        raise CorrespondenceViolation(
            f"Trace step maps to hostile TLA+ action {tla_action!r}. "
            f"This transition MUST have been blocked by the runtime."
        )

    trace.tla_action = tla_action
    return tla_action


# ---------------------------------------------------------------------------
# Verifier: no model gap (TLA+ -> Python direction)
# ---------------------------------------------------------------------------

def verify_no_model_gap() -> list[str]:
    """
    Return TLA+ system actions that have no Python correspondent.
    An empty list means the correspondence is complete in both directions.
    """
    covered = {v for v in PYTHON_TO_TLA.values() if v not in _HOSTILE_TLA_ACTIONS}
    return sorted(_TLA_SYSTEM_ACTIONS - covered)


# ---------------------------------------------------------------------------
# Verifier: mutation surface audit (AST scan of service.py)
# ---------------------------------------------------------------------------

def verify_mutation_surface(service_py_path: str) -> list[str]:
    """
    AST-scan service.py and return the names of any ConsequenceExecutionEvent
    write sites that are NOT in KNOWN_MUTATION_SITES.

    We look for functions that:
      - contain `db.add(ce)` where `ce` is a ConsequenceExecutionEvent instance
    and map them to their enclosing function name.

    The implementation tags each ConsequenceExecutionEvent construction with a
    comment marker _SITE:<name> so the AST scan can identify the actor class.
    If no marker exists, the site is reported as unregistered.
    """
    src = open(service_py_path, encoding="utf-8").read()
    lines = src.splitlines()

    unregistered = []
    # Simple heuristic: find all lines that assign ConsequenceExecutionEvent(
    # and look backwards for a _SITE: comment tag.
    for i, line in enumerate(lines):
        if "ConsequenceExecutionEvent(" in line and "ce = " in line:
            # Search upward for a site tag comment
            site_name = None
            for j in range(i - 1, max(0, i - 10), -1):
                if "# _SITE:" in lines[j]:
                    site_name = lines[j].split("# _SITE:")[1].strip()
                    break
            if site_name is None:
                # Fall back to enclosing function name via AST
                site_name = _enclosing_function(src, i + 1)
            if site_name not in KNOWN_MUTATION_SITES:
                unregistered.append(f"L{i+1}: site={site_name!r} not in KNOWN_MUTATION_SITES")
    return unregistered


def _enclosing_function(src: str, lineno: int) -> str:
    tree = ast.parse(src)
    best = "unknown"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if hasattr(node, "lineno") and node.lineno <= lineno:
                best = node.name
    return best


# ---------------------------------------------------------------------------
# Trace extractor: read a normalized trace from DB events
# ---------------------------------------------------------------------------

def extract_traces(
    db_events: list,  # list of ConsequenceExecutionEvent ORM objects
    actor_map: dict[tuple[str, int], str],  # (operation_id, version) -> actor_class
) -> list[TruthTransitionTrace]:
    """
    Given a sequence of ConsequenceExecutionEvent rows for one operation_id,
    emit a TruthTransitionTrace for each state transition.
    """
    traces = []
    prev_state = "none"
    for ev in sorted(db_events, key=lambda e: e.version):
        traces.append(TruthTransitionTrace(
            operation_id=ev.operation_id,
            event_index=ev.version,
            previous_truth_state=prev_state,
            next_truth_state=ev.state,
            intent_hash=ev.intent_hash,
            consequence_identity=ev.receipt_id or "unknown",
            proof_type=ev.completion_proof_type or "none",
            proof_subject_hash=ev.proof_subject_hash or "none",
            actor_class=actor_map.get((ev.operation_id, ev.version), "unknown"),
        ))
        prev_state = ev.state
    return traces
