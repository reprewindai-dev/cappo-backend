from __future__ import annotations

import asyncio
import json

from cappo_backend.core.capi_pipeline import seal_evidence_pack


def test_governed_compute_seal_preserves_bounded_consequence_facts():
    result = {
        "response": "opaque/raw result not copied into summary",
        "provider": "lockerphycer-governed-cell",
        "model": None,
        "tokens": 0,
        "governed_cell": {
            "cell_id": "cell-1",
            "runtime": "podman",
            "authority_digest": "sha256:authority",
            "started_at": "2026-08-21T10:00:00Z",
            "completed_at": "2026-08-21T10:00:01Z",
            "network_mode": "none",
            "credential_mode": "brokered_only",
            "teardown_confirmed": True,
        },
        "effect": {
            "provider": "github",
            "operation": "github.file.update",
            "repository": "reprewindai-dev/sandbox",
            "branch": "main",
            "path": "README.md",
            "before_sha": "a" * 40,
            "after_blob_sha": "b" * 40,
            "commit_sha": "c" * 40,
            "effect_digest": "sha256:effect",
            "mutation_succeeded": True,
            "credential_revoked": True,
            "security_status": "COMPLETE",
            # A future adapter accidentally returning secret-like extra material
            # must not cause the PGL summary to persist it.
            "token": "must-not-be-sealed",
            "content_b64": "must-not-be-sealed",
        },
        "security_status": "COMPLETE",
        "credential_revocation_confirmed": True,
    }

    seal = asyncio.run(
        seal_evidence_pack(
            "evidence-1",
            result,
            request_evidence={"action": "github.file.update"},
        )
    )

    governed = seal["governed_compute"]
    assert governed["profile"] == "veklom-governed-compute-p0"
    assert governed["cell"]["network_mode"] == "none"
    assert governed["cell"]["teardown_confirmed"] is True
    assert governed["effect"]["before_sha"] == "a" * 40
    assert governed["effect"]["after_blob_sha"] == "b" * 40
    assert governed["effect"]["commit_sha"] == "c" * 40
    assert governed["credential_revocation_confirmed"] is True

    serialized = json.dumps(governed)
    assert "must-not-be-sealed" not in serialized
    assert "content_b64" not in serialized
    assert '"token"' not in serialized
