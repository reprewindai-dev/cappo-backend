import hashlib
import json
import os
import time
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
            "verifier_method": self.verifier_method,
            "measurement_digest": self.measurement_digest,
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
        
    def measure(self, connection_info: Dict[str, Any], challenge_nonce: str) -> Dict[str, Any]:
        raise NotImplementedError
        
    def verify(self, measurement: Dict[str, Any], challenge_nonce: str) -> bool:
        raise NotImplementedError
        
    def attest(self, measurement: Dict[str, Any]) -> VerifiedRuntimeContext:
        raise NotImplementedError

class ActiveVerifierRegistry:
    def __init__(self):
        self._modules: Dict[str, VerifierModule] = {}
        self._frozen: bool = False

    def register(self, name: str, module: VerifierModule):
        if self._frozen:
            raise RuntimeError(f"Registry is frozen, cannot register {name}")
        if name in self._modules:
            raise RuntimeError(f"Duplicate verifier name registration: {name}")
        self._modules[name] = module

    def freeze(self):
        """Freezes the registry preventing further registrations."""
        self._frozen = True

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
            
        nonce = target_module.challenge(connection_info)
        measurement = target_module.measure(connection_info, nonce)
        if not target_module.verify(measurement, nonce):
            raise ValueError("Active Verification failed: physical measurement rejected")
            
        context = target_module.attest(measurement)
        if context.challenge_nonce != nonce:
            raise ValueError("Active Verification failed: returned challenge nonce mismatch (replay protection fault)")
            
        if float(context.expires_at) < time.time():
            raise ValueError("Active Verification failed: Context expired")
            
        return context

registry = ActiveVerifierRegistry()

# Note: No global mock verifiers are registered in production.
# Tests should register their own mocks and then optionally freeze the registry.


import contextvars
_trusted_connection_info = contextvars.ContextVar('_trusted_connection_info', default={})

def get_trusted_physical_connection_info() -> Dict[str, Any]:
    return _trusted_connection_info.get()

# set_trusted_physical_connection_info is intentionally removed.
# Only trusted host middleware (e.g., PhysicalBoundaryMiddleware) may
# manipulate the underlying contextvar.
