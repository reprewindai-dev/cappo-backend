def patch_service():
    with open("cappo_backend/capability_mount/service.py", "r", encoding="utf-8") as f:
        content = f.read()
        
    old = '''        context = ConsequenceContext(
            action=action,
            resource=resource,
            arguments=arguments,
            operation_id=op_id,
        )'''
    new = '''        context = ConsequenceContext(
            action=action,
            resource=resource,
            arguments=arguments,
            operation_id=op_id,
            workspace=row.owner_workspace,
        )'''
        
    if old in content:
        content = content.replace(old, new)
        with open("cappo_backend/capability_mount/service.py", "w", encoding="utf-8") as f:
            f.write(content)
        print("Success")
    else:
        print("Failed to find old block")

patch_service()
