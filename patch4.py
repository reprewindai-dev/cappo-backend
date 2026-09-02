with open('cappo_backend/api/routers/exec_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# find expected_auth_hash = hashlib.sha256(
start = content.find('                expected_auth_hash = hashlib.sha256(')
if start == -1:
    print("Cannot find start")
    exit(1)

end_str = 'expected_policy_decision_hash=auth.policy_decision_hash,\n                )'
end = content.find(end_str, start)
if end == -1:
    print("Cannot find end")
    exit(1)
end += len(end_str)

new_code = '''                # Use Explicit Compatibility Adapter for Legacy Credentials
                adapter = LegacyCredentialAdapter(db)
                translated_lease_ref = adapter.translate(auth, wit, ect, wpt)
                # If translation succeeds, we assign it so that it goes through the canonical lease path
                body.capability_lease = translated_lease_ref'''

content = content[:start] + new_code + content[end:]

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
