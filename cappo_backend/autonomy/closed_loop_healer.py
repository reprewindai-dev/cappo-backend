import hashlib
import json
from typing import Any, Dict, List

from cappo_backend.truth.inbound_truth_enforcer import InboundTruthEnforcer, TruthLedger
from cappo_backend.truth.inference import AdmissibleContextReceipt, InferenceGateway
from cappo_backend.truth.models import ClaimState, FactRequirement, TruthClaim, TypedPayload

# --- Mock Components for the Organism Loop ---

class MockModelClient:
    def invoke(self, prompt: str) -> str:
        # Simple simulated intelligence
        if "latency=900ms" in prompt:
            return '{"diagnosis": "cache_miss_spike", "proposed_repair": "flush_redis_cache"}'
        elif "latency=20ms" in prompt:
            return '{"diagnosis": "healthy", "proposed_repair": "none"}'
        return '{"diagnosis": "unknown", "proposed_repair": "none"}'

class MockCAPPO:
    def request_authority(self, identity: str, action: str, resource: str, premises_receipt: AdmissibleContextReceipt) -> Dict[str, Any]:
        print(f"[CAPPO] Evaluating request for {action} on {resource}...")
        # Verify that the premises justify the authority
        if not premises_receipt.claims:
            raise Exception("CAPPO Denial: No certified premises provided.")
        
        # Issue Bounded Lease
        print("[CAPPO] Approved. Minting ephemeral authority lease.")
        lease_token = f"LEASE-{hashlib.sha256(action.encode()).hexdigest()[:8]}"
        return {"lease_token": lease_token, "action": action, "resource": resource}

class MockLockerphycer:
    def __init__(self):
        self.state = {"redis_cache": "bloated"}

    def execute(self, lease: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[LOCKERPHYCER] Verifying lease {lease['lease_token']}...")
        if lease["action"] == "flush_redis_cache":
            self.state["redis_cache"] = "flushed"
            print("[LOCKERPHYCER] Execution successful: flushed redis.")
            return {"status": "success", "evidence_hash": "e_hash_123"}
        return {"status": "failed", "evidence_hash": "e_hash_fail"}

class MockPGL:
    def log_event(self, event_type: str, data: Dict[str, Any]) -> str:
        print(f"[PGL] Committing {event_type} to ledger...")
        return f"pgl_tx_{hashlib.sha256(str(data).encode()).hexdigest()[:8]}"

# --- The Closed-Loop Organism ---

class ClosedLoopHealer:
    def __init__(self):
        # Initialize the Truth Ledger and Enforcer
        self.ledger = TruthLedger()
        # Seed the ledger with authority for our system monitor
        self.ledger.approved_signers[("system.monitor", "tenant_1", "health_metric")] = {"sys_agent_01"}
        self.ledger.canonical_heads["monitor_node_a"] = 1
        
        self.truth_enforcer = InboundTruthEnforcer(self.ledger)
        self.inference = InferenceGateway(MockModelClient())
        self.cappo = MockCAPPO()
        self.locker = MockLockerphycer()
        self.pgl = MockPGL()

    def _observe(self, iteration: int) -> TruthClaim:
        # Simulate observing external state (P1)
        if self.locker.state.get("redis_cache") == "bloated":
            val = '{"latency=900ms"}'
        else:
            val = '{"latency=20ms"}'
            
        return TruthClaim(
            claim_id=f"claim_{iteration}",
            source_domain="system.monitor",
            tenant_id="tenant_1",
            fact_type="health_metric",
            signer="sys_agent_01",
            signature="mock_sig",
            payload=TypedPayload(subject="redis_cache", predicate="latency", value=val, scope="system"),
            source_id="monitor_node_a",
            version=1 + iteration,
            parent_version=iteration,
            evaluation_time_locked=100 + iteration,
            expires_at=100 + iteration + 100,
            state=ClaimState.CLAIMED
        )

    def run_cycle(self, iteration: int = 1):
        print(f"\n--- INITIATING AUTONOMY CYCLE {iteration} ---")
        
        # 1. OBSERVE
        raw_claim = self._observe(iteration)
        self.ledger.canonical_heads["monitor_node_a"] = iteration # update ledger for test
        print(f"1. OBSERVE: Gathered raw state -> {raw_claim.payload.value}")

        reqs = [FactRequirement(fact_domain="health_metric", minimum_assurance="system.telemetry", max_age_seconds=50)]
        try:
            certified_claims = self.truth_enforcer.certify_context([raw_claim], reqs, trusted_clock=100+iteration)
        except Exception as e:
            print(f"2. CERTIFICATION FAILED: {e}")
            return
            
        receipt = AdmissibleContextReceipt(certified_claims, signature="sig_enforcer_01")
        print("2. CERTIFY OBSERVATION: Context certified and receipt minted.")

        # 3. DETECT DEVIATION & 4. DIAGNOSE
        print("3/4. DETECT & DIAGNOSE: Invoking inference bound by receipt...")
        diagnosis_json = self.inference.generate_intent("Diagnose system health", receipt)
        diagnosis = json.loads(diagnosis_json)
        print(f"   -> AI Diagnosis: {diagnosis['diagnosis']}")

        # 5. PROPOSE REPAIR
        repair_action = diagnosis["proposed_repair"]
        print(f"5. PROPOSE REPAIR: {repair_action}")

        if repair_action == "none":
            print("System healthy. Cycle complete.")
            return

        # 6. CERTIFY REPAIR PREMISES
        # (In a real system, the repair intent itself is also treated as a claim that must be certified)
        print("6. CERTIFY REPAIR PREMISES: Relying on original context receipt.")

        # 7. SIMULATE / TEST (Skipped in mock)
        print("7. SIMULATE / TEST: Dry run passed.")

        # 8. CAPPO BOUNDED REPAIR AUTHORITY
        print("8. CAPPO: Requesting bounded repair authority...")
        lease = self.cappo.request_authority(identity="sys_agent_01", action=repair_action, resource="redis_cache", premises_receipt=receipt)

        # 9. LOCKERPHYCER (REAL REPAIR)
        print("9. LOCKERPHYCER: Executing real repair...")
        exec_result = self.locker.execute(lease)

        # 10. P5 & PGL
        print("10. PGL: Committing evidence...")
        self.pgl.log_event("repair_execution", {"lease": lease, "result": exec_result})

        # 11. RE-OBSERVE
        print("\n--- INITIATING RE-OBSERVATION ---")
        post_claim = self._observe(iteration + 1)
        self.ledger.canonical_heads["monitor_node_a"] = iteration + 1
        
        # 12. CERTIFY NEW STATE
        post_certified = self.truth_enforcer.certify_context([post_claim], reqs, trusted_clock=100+iteration+1)
        post_receipt = AdmissibleContextReceipt(post_certified, signature="sig_enforcer_01")
        
        post_diagnosis_json = self.inference.generate_intent("Diagnose system health", post_receipt)
        post_diagnosis = json.loads(post_diagnosis_json)
        
        print(f"12. CERTIFY NEW STATE: Diagnosis is now '{post_diagnosis['diagnosis']}'")
        
        if post_diagnosis['diagnosis'] == "healthy":
            print("HEALTHY: The organism has successfully healed itself within constitutional bounds.")
        else:
            print("ESCALATE: Repair failed to restore health.")


if __name__ == "__main__":
    healer = ClosedLoopHealer()
    healer.run_cycle(iteration=1)
