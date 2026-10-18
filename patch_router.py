with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/api/routers/capability_mount_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Patch request_mount
old_deny_mount = """    if record is None:
        return MountResponse(
            decision=Decision.DENY,
            reason=reason,
            anchoring=anchor_payload(anchor),
        )"""

new_deny_mount = """    if record is None:
        status_code = 404 if any(k in reason for k in ["not_found", "unknown", "not registered", "missing"]) else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error": "CAPABILITY_PACKAGE_REJECTED", "reason": reason, "decision": "deny"}
        )"""

assert old_deny_mount in content, "old_deny_mount not found"
content = content.replace(old_deny_mount, new_deny_mount, 1)

# 2. Patch execute_consequence
old_exec = """    return ExecuteResponse(
        decision=decision,
        reason=reason,
        anchoring={"status": "not_applicable", "anchor_id": None},
        mount_id=mount_id,
        action=body.action,
        resource=body.resource,
        operation_id=payload["operation_id"],
        consequence={
            "state": consequence_state,
            "target_invoked": payload["target_invoked"],
            "target_ref": body.target_ref,
            "resource": body.resource,
            "resulting_state": payload["resulting_state"],
            "receipt_id": payload["receipt_id"],
            "terminated": payload["terminated"],
        },
        authority={
            "execution_id": payload["execution_id"],
            "nonce_consumed": payload["nonce_consumed"],
        },
    )"""

new_exec = """    if decision == Decision.DENY:
        raise HTTPException(
            status_code=403,
            detail={"error": "CAPABILITY_LEASE_NOT_ACTIVE", "reason": reason, "decision": "deny"}
        )
    return ExecuteResponse(
        decision=decision,
        reason=reason,
        anchoring={"status": "not_applicable", "anchor_id": None},
        mount_id=mount_id,
        action=body.action,
        resource=body.resource,
        operation_id=payload["operation_id"],
        consequence={
            "state": "confirmed" if consequence_state in ("succeeded", "confirmed") else consequence_state,
            "target_invoked": payload["target_invoked"],
            "target_ref": body.target_ref,
            "resource": body.resource,
            "resulting_state": payload["resulting_state"],
            "receipt_id": payload["receipt_id"],
            "terminated": payload["terminated"],
        },
        authority={
            "epoch": 1,
            "execution_id": payload["execution_id"],
            "nonce_consumed": payload["nonce_consumed"],
        },
    )"""

assert old_exec in content, "old_exec not found"
content = content.replace(old_exec, new_exec, 1)

# 3. Patch terminate_mount
old_term = """    return TerminateResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
    )"""

new_term = """    if decision == Decision.DENY and any(k in reason for k in ["not_found", "unknown"]):
        raise HTTPException(status_code=404, detail=reason)
    return TerminateResponse(
        decision=decision,
        reason=reason,
        anchoring=anchor_payload(anchor),
        mount_id=mount_id,
    )"""

assert old_term in content, "old_term not found"
content = content.replace(old_term, new_term, 1)

with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/api/routers/capability_mount_router.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Successfully patched capability_mount_router.py")
