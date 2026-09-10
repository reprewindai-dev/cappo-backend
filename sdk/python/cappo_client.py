from typing import Any, Dict, Optional, List
import requests
import json
import time
from dataclasses import dataclass

@dataclass
class MountResponse:
    mount_id: str
    execution_id: str
    token_id: str
    nonce: str
    bearer: str

@dataclass
class ExecResponse:
    status: str
    finality_state: str

class CappoClient:
    def __init__(self, base_url: str, bearer_token: Optional[str] = None, timeout_s: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout_s
        self.session = requests.Session()
        if self.bearer_token:
            self.session.headers.update({"Authorization": f"Bearer {self.bearer_token}"})
            
    def set_api_key(self, api_key: str):
        self.session.headers.update({
            "X-API-Key": api_key,
            "X-Wallet-Address": "test-wallet",
            "X-Workspace-ID": "test-workspace"
        })

    def mount_capability(
        self,
        package_ref: str,
        execution_id: str,
        execution_scope: Dict[str, Any],
        requested_action_scope: Dict[str, List[str]],
        ttl_seconds: int = 300,
    ) -> MountResponse:
        url = f"{self.base_url}/v1/capability/mounts"
        payload = {
            "package_ref": package_ref,
            "execution_id": execution_id,
            "execution_scope": execution_scope,
            "requested_action_scope": requested_action_scope,
            "ttl_seconds": ttl_seconds,
        }
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("decision") != "allow":
            raise ValueError(f"Mount denied: {data.get('reason')}")
        
        token_info = data["token"]
        return MountResponse(
            mount_id=data["mount"]["id"],
            execution_id=token_info["execution_id"],
            token_id=token_info["token_id"],
            nonce=token_info["nonce"],
            bearer=f"biscuit:{token_info['token_id']}:{token_info['nonce']}",
        )

    def dispatch_consequence(
        self,
        capability_lease: MountResponse,
        action: str,
        resource: str,
        prompt: str,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/consequence/dispatch"
        payload = {
            "execution_id": capability_lease.execution_id,
            "action": action,
            "resource": resource,
            "mount_id": capability_lease.mount_id,
            "prompt": prompt,
        }
        # The executor uses the token material, but the client passes the handle via the API.
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if not resp.ok:
            print("DISPATCH ERR:", resp.text)
        resp.raise_for_status()
        return resp.json()

    def reconcile_consequence(self, execution_id: str, simulate_target_503: bool = False) -> ExecResponse:
        url = f"{self.base_url}/v1/consequence/reconcile"
        resp = self.session.post(url, json={"execution_id": execution_id, "simulate_target_503": simulate_target_503}, timeout=self.timeout)
        if resp.status_code == 503:
            raise RuntimeError("RECONCILIATION_UNAVAILABLE")
        resp.raise_for_status()
        data = resp.json()
        return ExecResponse(
            status=data.get("status", "RECONCILED_SUCCEEDED"),
            finality_state="TERMINAL"
        )
