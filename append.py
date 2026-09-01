with open('tests/capability_mount/test_execute_consequence.py', 'a') as f:
    f.write('''

def test_token_mismatch_is_denied_and_mount_survives(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    bad_payload = execute_payload(mount)
    bad_payload["token_id"] = "wrong-token"
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=bad_payload,
    ).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "token_mismatch"
    assert adapter.invocation_count == 0

    # Mount survives preflight denial: Corrected request succeeds
    success_body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert success_body["decision"] == "allow"
    assert adapter.invocation_count == 1

def test_unknown_target_survives(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, target_ref="missing.target"),
    ).json()
    assert body["decision"] == "deny"

    # Mount survives
    success_body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert success_body["decision"] == "allow"
    assert adapter.invocation_count == 1

def test_missing_action_survives(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path, CreateOnlyAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, action="record.delete"),
    ).json()
    assert body["decision"] == "deny"

    success_body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, action="record.create"),
    ).json()
    assert success_body["decision"] == "allow"
    assert adapter.invocation_count == 1

def test_traversal_survives(
    client: TestClient, tmp_path: Path
) -> None:
    mount, adapter = prepare(client, tmp_path)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="../admin"),
    ).json()
    assert body["decision"] == "deny"

    success_body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount),
    ).json()
    assert success_body["decision"] == "allow"
    assert adapter.invocation_count == 1
'''
    )
