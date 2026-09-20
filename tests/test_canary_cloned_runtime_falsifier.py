from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import (
    ConsequenceContext,
    GovernedCounterAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.service import (
    GOVERNED_COUNTER_PACKAGE,
    AnchorResult,
)
from cappo_backend.config import Settings
from cappo_backend.main import create_app


class MockPGLAnchor:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def anchor(self, event_type: str, **payload: object) -> AnchorResult:
        self.events.append({"event_type": event_type, **payload})
        return AnchorResult("confirmed", anchor_id=f"canary-anchor-{len(self.events)}")


def configure_canary_environment(client: TestClient, root: Path) -> GovernedCounterAdapter:
    registry = client.app.state.mount_registry
    registry.register_package(GOVERNED_COUNTER_PACKAGE)
    registry.anchor = MockPGLAnchor()
    adapter = GovernedCounterAdapter(root)
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(GovernedCounterAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "canary-ws-1"
    return adapter


def test_canary_cloned_runtime_falsification_invariant(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    FALSIFICATION TEST: Cloned Runtime Possession != Consequence Authority.
    
    Invariant Under Test:
      possession(cloned_runtime_state, credentials) != authority_to_mutate_consequence
      Delta_C_stale == 0
      
    Execution Sequence:
      1. Mount veklom.governed-counter@v1 on Primary.
      2. Verify baseline target state via independent readback endpoint (C_0 = 0).
      3. Primary executes legitimate operation 1 -> counter advances to C_1 = 1.
      4. Simulate adversary cloning the running runtime container state:
         Adversary extracts held mount ID, tokens, nonces, and scope.
      5. Primary terminates or revokes the capability mount (/terminate).
      6. Cloned runtime fires hostile counter.increment attack using cloned credentials.
      7. Enforcement Gate rejects with decision == 'deny' (DENY_TERMINATED / stale).
      8. Independent Discriminator: Query target state via independent readback endpoint.
         Verify counter remains exactly 1 (Delta_C_stale == 0).
    """
    adapter = configure_canary_environment(client, tmp_path)
    resource_id = "falsifier-counter-001"

    # Step 1: Primary Mounts Capability
    mount_res = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": GOVERNED_COUNTER_PACKAGE.id,
            "execution_scope": {"workspace": "canary-ws-1", "project": "canary-p1"},
            "requested_action_scope": {
                "reads": ["counter.read"],
                "writes": ["counter.increment"],
                "blocked": ["counter.reset"],
            },
            "ttl_seconds": 300,
        },
    )
    assert mount_res.status_code == 200
    mount_data = mount_res.json()
    assert mount_data["decision"] == "allow"
    mount_id = mount_data["mount"]["id"]
    primary_token = mount_data["token"]

    # Step 2: Baseline Independent Readback
    baseline_read = client.get(
        f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
        params={"resource": resource_id, "mount_id": mount_id},
    )
    assert baseline_read.status_code == 200
    baseline_state = baseline_read.json()["state"]
    assert baseline_state["value"] == 0
    assert baseline_state["version"] == 0

    # Step 3: Primary Executes Legitimate Operation 1
    legit_exec = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": primary_token["token_id"],
            "nonce": primary_token["nonce"],
            "action": "counter.increment",
            "target_ref": GovernedCounterAdapter.ref,
            "resource": resource_id,
            "arguments": {"by": 1},
            "operation_id": "op-legit-ep1",
        },
    )
    assert legit_exec.status_code == 200
    legit_body = legit_exec.json()
    assert legit_body["decision"] == "allow"
    assert legit_body["consequence"]["resulting_state"]["value"] == 1
    assert legit_body["consequence"]["resulting_state"]["version"] == 1

    # Step 4: Clone Runtime State to Secondary Adversary
    # The cloned runtime possesses identical memory/credential snapshot
    cloned_mount_id = mount_id
    cloned_token = copy.deepcopy(primary_token)

    # Step 5: Trigger Termination / Revocation on Primary
    term_res = client.post(
        f"/v1/capability/mounts/{mount_id}/terminate",
        json={"reason": "epoch_advance_or_host_revocation"},
    )
    assert term_res.status_code == 200
    assert term_res.json()["decision"] == "allow"

    # Step 6: Hostile Attack from Cloned Runtime Instance
    # Adversary attempts to advance counter using cloned credentials
    attack_res = client.post(
        f"/v1/capability/mounts/{cloned_mount_id}/execute",
        json={
            "token_id": cloned_token["token_id"],
            "nonce": cloned_token["nonce"],
            "action": "counter.increment",
            "target_ref": GovernedCounterAdapter.ref,
            "resource": resource_id,
            "arguments": {"by": 1},
            "operation_id": "op-hostile-cloned-zombie-001",
        },
    )
    assert attack_res.status_code == 200
    attack_body = attack_res.json()

    # Step 7: Gate Fencing Verification
    assert attack_body["decision"] == "deny", (
        f"CRITICAL FAILURE: Cloned runtime was allowed to mutate state! Body: {attack_body}"
    )
    assert "terminated" in attack_body.get("reason", "").lower() or "deny" in attack_body.get("decision", "").lower()

    # Step 8: Independent Discriminator Readback
    # Verify that the physical counter was NOT incremented by the cloned attack
    discriminator_read = client.get(
        f"/v1/capability/targets/{GovernedCounterAdapter.ref}/state",
        params={"resource": resource_id, "mount_id": mount_id},
    )
    assert discriminator_read.status_code == 200
    final_state = discriminator_read.json()["state"]

    # Falsification Pass Criterion:
    # Delta C_stale == 0 (Counter value must remain exactly 1)
    assert final_state["value"] == 1, (
        f"FALSIFICATION FAILED: Counter value changed to {final_state['value']} after terminated attack! "
        f"Expected exactly 1 (Delta C == 0)."
    )
    assert final_state["version"] == 1, (
        f"FALSIFICATION FAILED: Counter version changed to {final_state['version']}!"
    )
