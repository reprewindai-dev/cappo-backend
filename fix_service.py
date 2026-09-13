def fix_service():
    with open('cappo_backend/capability_mount/service.py', 'r', encoding='utf-8') as f:
        content = f.read()

    old_sig = '''    def execute_consequence(
        self,
        mount_id: str,
        action: str,
        *,
        token_id: str,
        nonce: str,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
        approval_token: str | None = None,
        suppression_evidence: str | None = None,
        suppression_confirmed: bool = False,
        target_ref: str,
        resource: str,
        arguments: dict[str, Any],
        operation_id: str | None = None,
    ) -> tuple[Decision, str, str | None, dict[str, Any]]:'''

    new_sig = '''    def execute_consequence(
        self,
        mount_id: str,
        action: str,
        *,
        token_id: str,
        nonce: str,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
        approval_token: str | None = None,
        suppression_evidence: str | None = None,
        suppression_confirmed: bool = False,
        target_ref: str,
        resource: str,
        arguments: dict[str, Any],
        operation_id: str | None = None,
        spiffe_fields: dict[str, Any] | None = None,
    ) -> tuple[Decision, str, str | None, dict[str, Any]]:'''

    old_eval = '''            dec, reason, _, detail = self.evaluate(
                mount_id=mount.id,
                action=action,
                resource=str(kwargs.get("resource")) if "resource" in kwargs else None,
                token_id=token.token_id,
                nonce=token.nonce,
                owner_principal=row.owner_principal,
                owner_workspace=row.owner_workspace,
                approval_token=str(kwargs.get("approval_token")) if "approval_token" in kwargs else None,
                suppression_evidence=str(kwargs.get("suppression_evidence")) if "suppression_evidence" in kwargs else None,
                suppression_confirmed=bool(kwargs.get("suppression_confirmed")),
            )'''

    new_eval = '''            dec, reason, _, detail = self.evaluate(
                mount_id=mount.id,
                action=action,
                resource=str(kwargs.get("resource")) if "resource" in kwargs else None,
                token_id=token.token_id,
                nonce=token.nonce,
                owner_principal=row.owner_principal,
                owner_workspace=row.owner_workspace,
                approval_token=str(kwargs.get("approval_token")) if "approval_token" in kwargs else None,
                suppression_evidence=str(kwargs.get("suppression_evidence")) if "suppression_evidence" in kwargs else None,
                suppression_confirmed=bool(kwargs.get("suppression_confirmed")),
                spiffe_fields=spiffe_fields,
            )'''

    if old_sig in content and old_eval in content:
        content = content.replace(old_sig, new_sig).replace(old_eval, new_eval)
        with open('cappo_backend/capability_mount/service.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Fixed service!")
    else:
        print("Could not find service blocks!")
        if old_sig not in content: print("Missing sig")
        if old_eval not in content: print("Missing eval")

fix_service()
