import re

with open(r"C:\Users\antho\.windsurf\cappo-backend\cappo_backend\api\routers\exec_router.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Strip authority_payload and _get_b64_json("Veklom-Authority")
content = content.replace(
    'authority_payload = _get_b64_json(request.headers, "Veklom-Authority")',
    'authority_payload = None  # Deprecated client JSON authority format'
)
content = content.replace(
    'authority_payload=authority_payload,',
    '# authority_payload=authority_payload, (Dropped: client authority is untrusted)'
)

# 2. Add verification of the server-side biscuit
replacement = """        # TRUSTED ASSERTION SEAM:
        # Strip client-supplied authority. We mint/retrieve the authoritative machine 
        # token exclusively from the server-side registry.
        biscuit_token = record.token.biscuit_token
        
        if not biscuit_token:
            raise HTTPException(
                status_code=403,
                detail={"error": "CRYPTOGRAPHIC_AUTHORITY_REQUIRED", "detail": "No server-side capability token found."},
            )
            
        from cappo_backend.security.biscuit import verify_biscuit_capability
        try:
            # Cryptographic boundary: verify the Biscuit signature and claims
            is_valid = verify_biscuit_capability(
                token_b64=biscuit_token,
                executor_spiffe_id=request.scope.get("executor_spiffe_id") or "any",
                action=action,
                resource=lifecycle_resource,
                subject_spiffe_id=principal,
            )
            if not is_valid:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "EXECUTION_AUTHORITY_DENIED", "detail": "Cryptographic authority verification failed"},
                )
        except Exception as e:
            raise HTTPException(
                status_code=403,
                detail={"error": "EXECUTION_AUTHORITY_DENIED", "detail": f"Invalid token format or signature: {e}"},
            )
"""
content = re.sub(
    r'\s*biscuit_token = request\.headers\.get\("Veklom-Authority"\)\s*',
    '\n' + replacement + '\n',
    content
)

with open(r"C:\Users\antho\.windsurf\cappo-backend\cappo_backend\api\routers\exec_router.py", "w", encoding="utf-8") as f:
    f.write(content)
