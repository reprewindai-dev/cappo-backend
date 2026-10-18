$f = "scripts\n8n_19_public_ingress_probe.py"
$c = Get-Content $f -Raw
$c = $c -replace "(?s)    key_record = kms\._get_key\(\).*?algorithm='HS256', headers=\{'kid': key_record\.key_id\}\)", "from cappo_backend.execution.kms import MockHardwareSecurityModule, KMSKeyRecord, KMSKeyStatus
    from cappo_backend.db.session import SessionLocal
    with SessionLocal() as db:
        record = db.query(KMSKeyRecord).filter(KMSKeyRecord.status == KMSKeyStatus.ACTIVE).first()
        active_kid = record.kid
    keys = MockHardwareSecurityModule._load_keys()
    private_bytes = bytes.fromhex(keys[active_kid])
    from cryptography.hazmat.primitives.asymmetric import ed25519
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
    claims_exp = base_claims.copy()
    claims_exp.update({
        'iss': 'cappo.veklom.com',
        'aud': 'sandbox_file_append',
        'iat': int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 86400,
        'exp': int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 3600,
        'jti': uuid.uuid4().hex
    })
    token_exp = jwt.encode(claims_exp, private_key, algorithm='EdDSA', headers={'kid': active_kid})"
Set-Content $f -Value $c
