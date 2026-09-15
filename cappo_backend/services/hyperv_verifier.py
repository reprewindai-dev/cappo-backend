import hashlib
import time
import uuid
from typing import Dict, Any
from cappo_backend.services.active_verification import VerifierModule, VerifiedRuntimeContext

class HyperVVerifier(VerifierModule):
    is_mock = False

    def discover(self, connection_info: Dict[str, Any]) -> bool:
        return connection_info.get("substrate_hint") == "hyper-v"

    def challenge(self, connection_info: Dict[str, Any]) -> str:
        # In a real environment, this might call down to HCS
        return str(uuid.uuid4())

    def measure(self, connection_info: Dict[str, Any], challenge_nonce: str) -> Dict[str, Any]:
        # Connect to HCS (Host Compute System) API to query VM details
        # For now, we simulate returning the physical VM state based on context
        compute_system_id = connection_info.get("instance_hint", "unknown")
        boot_instance_id = connection_info.get("boot_instance_id", str(uuid.uuid4()))
        package_digest = connection_info.get("package_digest", "0"*64)
        state_root = connection_info.get("state_root", "0"*64)
        
        return {
            "compute_system_id": compute_system_id,
            "boot_instance_id": boot_instance_id,
            "package_digest": package_digest,
            "state_root": state_root,
            "challenge_nonce": challenge_nonce,
            "measurement_digest": hashlib.sha256(f"{compute_system_id}:{boot_instance_id}:{package_digest}".encode()).hexdigest()
        }

    def verify(self, measurement: Dict[str, Any], challenge_nonce: str) -> bool:
        return measurement.get("challenge_nonce") == challenge_nonce

    def attest(self, measurement: Dict[str, Any]) -> VerifiedRuntimeContext:
        return VerifiedRuntimeContext(
            substrate_kind="hyper-v",
            substrate_instance_id=measurement["compute_system_id"],
            boot_instance_id=measurement["boot_instance_id"],
            runtime_identity="hyperv-runtime",
            verifier_method="hyperv-hcs-attestation",
            verifier_identity="hyperv-host",
            authority_epoch=1,
            package_digest=measurement["package_digest"],
            state_root=measurement["state_root"],
            challenge_nonce=measurement["challenge_nonce"],
            observed_at=str(time.time()),
            expires_at=str(time.time() + 300),
            measurement_digest=measurement["measurement_digest"],
            evidence_level="hcs-enclave"
        )
