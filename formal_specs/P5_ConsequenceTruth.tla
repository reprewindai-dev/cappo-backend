--------------------------- MODULE P5_ConsequenceTruth ---------------------------
EXTENDS Naturals, FiniteSets, Sequences, TLC

CONSTANTS 
    Operations,     \* Set of unique operations
    Intents,        \* Set of unique intent hashes
    Consequences,   \* Set of consequence identities
    ProofTypes      \* Set of proof types

VARIABLES 
    state,          \* state[op] = current consequence state
    proof,          \* proof[op] = the proof type that justified the current state
    intent,         \* intent[op] = the intent hash bound to the operation
    conseq,         \* conseq[op] = consequence identity
    has_truth_auth, \* has_truth_auth[op] = boolean
    replayed        \* replayed[op] = boolean tracking if proof was replayed

\* States
Authorized == "AUTHORIZED"
Started    == "STARTED"
Unknown    == "OUTCOME_UNKNOWN"
Succeeded  == "SUCCEEDED"
Failed     == "FAILED"
ReconciledSucceeded == "RECONCILED_SUCCEEDED"
ReconciledFailed    == "RECONCILED_FAILED"

TerminalStates == {Succeeded, Failed, ReconciledSucceeded, ReconciledFailed}
AllStates == {Authorized, Started, Unknown} \cup TerminalStates

\* Proof Certainties
Certainty(p) ==
    CASE p = "outcome_uncertain" -> 0
      [] p = "callback_return" -> 1
      [] p = "reconciliation_api_query" -> 2
      [] OTHER -> 0

ReqCertainty(s) ==
    CASE s = Succeeded -> 1
      [] s = Failed -> 1
      [] s = ReconciledSucceeded -> 2
      [] s = ReconciledFailed -> 2
      [] s = Unknown -> 0
      [] OTHER -> 0

-----------------------------------------------------------------------------
\* INITIALIZATION

Init == 
    /\ state = [op \in Operations |-> Authorized]
    /\ proof = [op \in Operations |-> "none"]
    /\ intent \in [Operations -> Intents]
    /\ conseq \in [Operations -> Consequences]
    /\ has_truth_auth \in [Operations -> BOOLEAN]
    /\ replayed = [op \in Operations |-> FALSE]

-----------------------------------------------------------------------------
\* SYSTEM TRANSITIONS

BeginConsequence(op) ==
    /\ state[op] = Authorized
    /\ state' = [state EXCEPT ![op] = Started]
    /\ UNCHANGED <<proof, intent, conseq, has_truth_auth, replayed>>

ReportCompletion(op, pt, succeeded) ==
    /\ state[op] = Started
    /\ Certainty(pt) >= 1
    /\ state' = [state EXCEPT ![op] = IF succeeded THEN Succeeded ELSE Failed]
    /\ proof' = [proof EXCEPT ![op] = pt]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

ReportUnknown(op) ==
    /\ state[op] = Started
    /\ state' = [state EXCEPT ![op] = Unknown]
    /\ proof' = [proof EXCEPT ![op] = "outcome_uncertain"]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

Reconcile(op, pt, succeeded) ==
    /\ state[op] = Unknown
    /\ has_truth_auth[op] = TRUE
    /\ Certainty(pt) >= 2
    /\ state' = [state EXCEPT ![op] = IF succeeded THEN ReconciledSucceeded ELSE ReconciledFailed]
    /\ proof' = [proof EXCEPT ![op] = pt]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

-----------------------------------------------------------------------------
\* ATTACKER ACTIONS

\* 1. ForgeState
ForgeState(op, target_state, pt) ==
    /\ target_state \in TerminalStates
    \* Defense rules:
    /\ target_state \in 
        (IF state[op] = Started THEN {Succeeded, Failed, Unknown}
         ELSE IF state[op] = Unknown THEN {ReconciledSucceeded, ReconciledFailed}
         ELSE {})
    /\ Certainty(pt) >= ReqCertainty(target_state)
    /\ (state[op] = Unknown => has_truth_auth[op] = TRUE)
    /\ state' = [state EXCEPT ![op] = target_state]
    /\ proof' = [proof EXCEPT ![op] = pt]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

\* 2. ReplayProof (Trying to use an old proof for a new operation)
ReplayProof(op1, op2) ==
    /\ state[op1] \in TerminalStates
    /\ state[op2] \in {Started, Unknown}
    \* Defense: Proposition binding requires matching intent & conseq
    /\ intent[op1] = intent[op2]
    /\ conseq[op1] = conseq[op2]
    /\ state' = [state EXCEPT ![op2] = state[op1]]
    /\ proof' = [proof EXCEPT ![op2] = proof[op1]]
    /\ replayed' = [replayed EXCEPT ![op2] = TRUE]
    /\ UNCHANGED <<intent, conseq, has_truth_auth>>

\* 3. SwapIntent
SwapIntent(op, new_intent) ==
    \* Defense: Intent cannot be modified once execution started
    /\ state[op] = Authorized
    /\ intent' = [intent EXCEPT ![op] = new_intent]
    /\ UNCHANGED <<state, proof, conseq, has_truth_auth, replayed>>

\* 4. SwapConsequence
SwapConsequence(op, new_conseq) ==
    /\ state[op] = Authorized
    /\ conseq' = [conseq EXCEPT ![op] = new_conseq]
    /\ UNCHANGED <<state, proof, intent, has_truth_auth, replayed>>

\* 5. BypassAuthority
BypassAuthority(op, pt) ==
    /\ state[op] = Unknown
    \* Defense: enforce has_truth_auth
    /\ has_truth_auth[op] = TRUE
    /\ Certainty(pt) >= 2
    /\ state' = [state EXCEPT ![op] = ReconciledSucceeded]
    /\ proof' = [proof EXCEPT ![op] = pt]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

\* 6. ResolveUnknownWithoutProof
ResolveUnknownWithoutProof(op) ==
    /\ state[op] = Unknown
    \* Defense: requires Certainty >= 2
    /\ Certainty("callback_return") >= 2  \* Will evaluate to FALSE
    /\ state' = [state EXCEPT ![op] = Succeeded]
    /\ UNCHANGED <<proof, intent, conseq, has_truth_auth, replayed>>

\* 7. RollbackEpoch
RollbackEpoch(op) ==
    /\ state[op] \in TerminalStates
    \* Defense: Append-only ledger means we cannot revert a terminal state to Started
    /\ FALSE
    /\ UNCHANGED <<state, proof, intent, conseq, has_truth_auth, replayed>>

\* 8. RaceReconcilers
RaceReconcilers(op, pt1, pt2) ==
    /\ state[op] = Unknown
    /\ has_truth_auth[op] = TRUE
    /\ Certainty(pt1) >= 2
    /\ Certainty(pt2) >= 2
    \* Simulate atomic CAS win
    /\ state' = [state EXCEPT ![op] = ReconciledSucceeded]
    /\ proof' = [proof EXCEPT ![op] = pt1]
    /\ UNCHANGED <<intent, conseq, has_truth_auth, replayed>>

\* 9. RaceExecutors
RaceExecutors(op) ==
    /\ state[op] = Authorized
    /\ state' = [state EXCEPT ![op] = Started]
    /\ UNCHANGED <<proof, intent, conseq, has_truth_auth, replayed>>

\* 10. SwapOperation
SwapOperation(op1, op2) ==
    \* Swapping operation ID to steal identity
    /\ FALSE \* Identity is intrinsic to the operation token in this model
    /\ UNCHANGED <<state, proof, intent, conseq, has_truth_auth, replayed>>

Next == 
    \E op \in Operations :
        \/ BeginConsequence(op)
        \/ \E pt \in ProofTypes :
            \/ ReportCompletion(op, pt, TRUE)
            \/ ReportCompletion(op, pt, FALSE)
            \/ Reconcile(op, pt, TRUE)
            \/ Reconcile(op, pt, FALSE)
            \/ \E s \in TerminalStates : ForgeState(op, s, pt)
            \/ BypassAuthority(op, pt)
        \/ ReportUnknown(op)
        \/ ResolveUnknownWithoutProof(op)
        \/ RollbackEpoch(op)
        \/ RaceExecutors(op)
        \/ \E op2 \in Operations : 
            \/ ReplayProof(op, op2)
            \/ SwapOperation(op, op2)
        \/ \E i \in Intents : SwapIntent(op, i)
        \/ \E c \in Consequences : SwapConsequence(op, c)
        \/ \E pt1, pt2 \in ProofTypes : RaceReconcilers(op, pt1, pt2)

-----------------------------------------------------------------------------
\* INVARIANTS (SAFETY)

NoOverclaim == 
    \A op \in Operations :
        (state[op] \in TerminalStates) => 
            /\ Certainty(proof[op]) >= ReqCertainty(state[op])
            /\ (state[op] \in {ReconciledSucceeded, ReconciledFailed} => has_truth_auth[op] = TRUE)

NoIllegalTruthTransition ==
    \A op \in Operations :
        state[op] \in AllStates

NoTruthWithoutAuthority ==
    \A op \in Operations :
        (state[op] \in {ReconciledSucceeded, ReconciledFailed}) => has_truth_auth[op] = TRUE

NoProofTransplant ==
    \A op1, op2 \in Operations :
        (op1 /= op2 /\ replayed[op2]) => (intent[op1] = intent[op2] /\ conseq[op1] = conseq[op2])

UnknownCannotSelfResolve ==
    \A op \in Operations :
        (state[op] = Unknown /\ proof[op] = "callback_return") => FALSE

HistoryCannotRewrite ==
    \A op \in Operations :
        state[op] \in AllStates

-----------------------------------------------------------------------------
\* PROPERTIES (LIVENESS)

Liveness == 
    \A op \in Operations :
        <>(state[op] \in TerminalStates \cup {Unknown})

=============================================================================
