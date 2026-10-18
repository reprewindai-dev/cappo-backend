def patch_counter_context():
    with open("tests/capability_mount/test_governed_counter.py", "r", encoding="utf-8") as f:
        content = f.read()

    old_context = '''def counter_context(
    action: str,
    *,
    resource: str = "demo-1",
    arguments: dict[str, object] | None = None,
) -> ConsequenceContext:
    return ConsequenceContext(
        action=action,
        resource=resource,
        arguments=arguments or {},
        operation_id=None,
    )'''

    new_context = '''def counter_context(
    action: str,
    *,
    resource: str = "demo-1",
    arguments: dict[str, object] | None = None,
    workspace: str | None = "w1"
) -> ConsequenceContext:
    return ConsequenceContext(
        action=action,
        resource=resource,
        arguments=arguments or {},
        operation_id=None,
        workspace=workspace,
    )'''

    if old_context in content:
        content = content.replace(old_context, new_context)
        with open("tests/capability_mount/test_governed_counter.py", "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched counter_context")

patch_counter_context()
