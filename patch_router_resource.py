with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/api/routers/capability_mount_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update ExecuteRequest resource field
old_exec_req = """class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    resource: str = Field(min_length=1)"""

new_exec_req = """class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    resource: str | None = None"""

assert old_exec_req in content, "old_exec_req not found"
content = content.replace(old_exec_req, new_exec_req, 1)

# 2. Update TerminateRequest
old_term_req = """class TerminateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: UnmountReason = UnmountReason.EXPLICIT_TERMINATE"""

new_term_req = """class TerminateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Any = "explicit_terminate" """

assert old_term_req in content, "old_term_req not found"
content = content.replace(old_term_req, new_term_req, 1)

# 3. Update execute_consequence function to default resource
old_exec_call = """    principal, workspace = _caller(request)
    decision, reason, consequence_state, payload = registry.execute_consequence(
        mount_id,
        body.action,
        token_id=body.token_id,
        nonce=body.nonce,
        owner_principal=principal,
        owner_workspace=workspace,
        approval_token=body.approval_token,
        suppression_evidence=body.suppression_evidence,
        suppression_confirmed=body.suppression_confirmed,
        target_ref=body.target_ref,
        resource=body.resource,"""

new_exec_call = """    principal, workspace = _caller(request)
    target_resource = body.resource or body.target_ref
    decision, reason, consequence_state, payload = registry.execute_consequence(
        mount_id,
        body.action,
        token_id=body.token_id,
        nonce=body.nonce,
        owner_principal=principal,
        owner_workspace=workspace,
        approval_token=body.approval_token,
        suppression_evidence=body.suppression_evidence,
        suppression_confirmed=body.suppression_confirmed,
        target_ref=body.target_ref,
        resource=target_resource,"""

assert old_exec_call in content, "old_exec_call not found"
content = content.replace(old_exec_call, new_exec_call, 1)

# 4. Update response resource in ExecuteResponse
old_exec_resp = """        mount_id=mount_id,
        action=body.action,
        resource=body.resource,"""

new_exec_resp = """        mount_id=mount_id,
        action=body.action,
        resource=target_resource,"""

assert old_exec_resp in content, "old_exec_resp not found"
content = content.replace(old_exec_resp, new_exec_resp, 1)

# 5. Update terminate_mount call
old_term_func = """    principal, workspace = _caller(request)
    decision, reason, anchor = registry.terminate(
        mount_id,
        body.reason,"""

new_term_func = """    principal, workspace = _caller(request)
    reason_val = str(body.reason)
    decision, reason, anchor = registry.terminate(
        mount_id,
        reason_val,"""

assert old_term_func in content, "old_term_func not found"
content = content.replace(old_term_func, new_term_func, 1)

with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/api/routers/capability_mount_router.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated capability_mount_router.py with optional resource and flexible reason")
