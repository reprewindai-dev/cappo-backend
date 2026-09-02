with open('cappo_backend/api/routers/exec_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

start_idx = content.find('            if authority_payload:')
if start_idx == -1:
    print("Cannot find start")
else:
    end_idx = content.find('            except IdentityValidationError', start_idx)
    if end_idx == -1:
        # Maybe it doesn't have try/except block immediately after in PR93?
        # Let's check where the enforcer finishes
        end_idx = content.find('            # cAPI PHASE 1', start_idx)
        if end_idx == -1:
            end_idx = content.find('    capi_payload', start_idx)
    
    if end_idx != -1:
        new_block = '''            if authority_payload:
                wit = WorkloadIdentityToken(**wit_payload) if wit_payload else None
                ect = ExecutionContextToken(**ect_payload) if ect_payload else None
                wpt = WorkloadProofToken(**wpt_payload) if wpt_payload else None
                auth_kwargs = dict(authority_payload)
                auth_kwargs.pop("_mock_hash", None)
                auth = AuthorityArtifact(**auth_kwargs)
                
                # Use Explicit Compatibility Adapter for Legacy Credentials
                adapter = LegacyCredentialAdapter(db)
                translated_lease_ref = adapter.translate(auth, wit, ect, wpt)
                # If translation succeeds, we assign it so that it goes through the canonical lease path
                body.capability_lease = translated_lease_ref\n\n'''
        
        content = content[:start_idx] + new_block + content[end_idx:]
        
        # Enforce canonical execution
        fallback_str = '        if lease_context is not None and lease_ref is not None:'
        if fallback_str in content:
            new_fallback = '''        if lease_ref is None:
            raise HTTPException(
                status_code=403,
                detail={"error": "CAPABILITY_LEASE_REQUIRED", "detail": "All governed execution must have a canonical lease context."}
            )

        if lease_context is not None and lease_ref is not None:'''
            content = content.replace(fallback_str, new_fallback)

        if 'LegacyCredentialAdapter' not in content:
            content = content.replace(
                'from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer',
                'from cappo_backend.authorization.cappo_auth import CappoPreauthorizationEnforcer\\nfrom cappo_backend.authorization.legacy_adapter import LegacyCredentialAdapter'
            )

        with open('cappo_backend/api/routers/exec_router.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Success")
    else:
        print("Cannot find end")
