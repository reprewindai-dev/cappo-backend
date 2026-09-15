import hashlib
import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional

@dataclass
class VerifiedRuntimeContext:
    substrate_kind: str
    substrate_instance_id: str
    boot_instance_id: str
    runtime_identity: str
    verifier_method: str
    verifier_identity: str
    authority_epoch: int
    package_digest: str
    state_root: str
    challenge_nonce: str
    observed_at: str
    expires_at: str
    measurement_digest: str
    evidence_level: str
    extra_claims: Optional[Dict] = None

    def get_full_evidence_hash(self) -> str:
        """Returns the full mint-time attestation/evidence commitment."""
        payload = asdict(self)
        serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()
        
    def get_runtime_incarnation_binding_hash(self) -> str:
        """Returns the stable execution identity (ignores time/nonce/freshness)."""
        payload = {
            "substrate_kind": self.substrate_kind,
            "substrate_instance_id": self.substrate_instance_id,
            "boot_instance_id": self.boot_instance_id,
            "runtime_identity": self.runtime_identity,
            "verifier_identity": self.verifier_identity,
            "package_digest": self.package_digest,
            "state_root": self.state_root,
            "authority_epoch": self.authority_epoch,
            "evidence_level": self.evidence_level,
        }
        serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

class VerifierModule:
    """SPI for all Active Verifiers"""
    is_mock: bool = False
    
    def discover(self, connection_info: Dict[str, Any]) -> bool:
        raise NotImplementedError
        
    def challenge(self, connection_info: Dict[str, Any]) -> str:
        raise NotImplementedError
        
    def measure(self, connection_info: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
        
    def verify(self, measurement: Dict[str, Any]) -> bool:
        raise NotImplementedError
        
    def attest(self, measurement: Dict[str, Any]) -> VerifiedRuntimeContext:
        raise NotImplementedError

class ActiveVerifierRegistry:
    def __init__(self):
        self._modules: Dict[str, VerifierModule] = {}

    def register(self, name: str, module: VerifierModule):
        if name in self._modules:
            raise RuntimeError(f"Duplicate verifier name registration: {name}")
        self._modules[name] = module

    def execute_active_verification(self, connection_info: Dict[str, Any], execution_mode: str = "live") -> VerifiedRuntimeContext:
        target_module = None
        for name, module in self._modules.items():
            if module.discover(connection_info):
                target_module = module
                break
                
        if not target_module:
            raise RuntimeError("No registered VerifierModule could establish physical identity for this runtime")
            
        if execution_mode == "live" and target_module.is_mock:
            raise RuntimeError("FATAL/DENY: Mock verifier forbidden in live execution mode")
            
        target_module.challenge(connection_info)
        measurement = target_module.measure(connection_info)
        if not target_module.verify(measurement):
            raise ValueError("Active Verification failed: physical measurement rejected")
            
        context = target_module.attest(measurement)
        import time
        if float(context.expires_at) < time.time():
            raise ValueError("Active Verification failed: Context expired")
            
        return context

registry = ActiveVerifierRegistry()

class HyperVVerifier(VerifierModule):
    is_mock = True
    
    def discover(self, connection_info: Dict[str, Any]) -> bool:
        return connection_info.get("substrate_hint") == "hyper-v"
        
    def challenge(self, connection_info: Dict[str, Any]) -> str:
        return "mock_nonce"
        
    def measure(self, connection_info: Dict[str, Any]) -> Dict[str, Any]:
        return {"hyperv_vm_id": connection_info.get("instance_hint", "VM-12345"), "epoch": 1}
        
    def verify(self, measurement: Dict[str, Any]) -> bool:
        return bool(measurement.get("hyperv_vm_id"))
        
    def attest(self, measurement: Dict[str, Any]) -> VerifiedRuntimeContext:
        import time
        return VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=measurement["hyperv_vm_id"],
            boot_instance_id="boot_hash_abc123",
            runtime_identity="hyperv-guest-identity",
            verifier_method="HOST_HYPERVISOR_API",
            verifier_identity="cappo-hyperv-host",
            authority_epoch=measurement["epoch"],
            package_digest="pkg-hash",
            state_root="state-root",
            challenge_nonce="mock_nonce",
            observed_at=str(time.time()),
            expires_at=str(time.time() + 3600),
            measurement_digest="measure-hash",
            evidence_level="HIGH_ASSURANCE"
        )

registry.register("hyper-v-mock", HyperVVerifier())
