def fix_router():
    with open('cappo_backend/api/routers/capability_mount_router.py', 'r', encoding='utf-8') as f:
        content = f.read()

    old_router = '''@router.post("/mounts/{mount_id}/execute", response_model=ExecuteResponse)
def execute_consequence(
    mount_id: str,
    body: ExecuteRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> ExecuteResponse:
    principal, workspace = _caller(request)
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
        resource=body.resource,
        arguments=body.arguments,
        operation_id=body.operation_id,
    )'''

    new_router = '''@router.post("/mounts/{mount_id}/execute", response_model=ExecuteResponse)
def execute_consequence(
    mount_id: str,
    body: ExecuteRequest,
    request: Request,
    registry: MountRegistry = Depends(get_registry),
) -> ExecuteResponse:
    principal, workspace = _caller(request)
    spiffe_fields = {
        "caller_spiffe_id": request.scope.get("caller_spiffe_id"),
        "trust_domain": request.scope.get("trust_domain"),
        "caller_cert_sha256": request.scope.get("caller_cert_sha256"),
        "svid_not_before": request.scope.get("svid_not_before"),
        "svid_not_after": request.scope.get("svid_not_after"),
        "eei_id": request.headers.get("x-veklom-eei-id"),
        "profile_id": request.headers.get("x-veklom-profile-id"),
        "lease_id": request.headers.get("x-veklom-lease-id"),
        "operator_id": request.headers.get("x-veklom-operator-id"),
    }
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
        resource=body.resource,
        arguments=body.arguments,
        operation_id=body.operation_id,
        spiffe_fields=spiffe_fields,
    )'''

    if old_router in content:
        content = content.replace(old_router, new_router)
        with open('cappo_backend/api/routers/capability_mount_router.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Fixed router!")
    else:
        print("Could not find old router block!")

fix_router()
