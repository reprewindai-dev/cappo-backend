with open('tests/test_g1_3_wan_off_scope_replay.py', 'r') as f:
    code = f.read()
code = code.replace('assert mount_record is not None', 'assert mount_record is not None, f\"Mount failed: {reason}\"')
with open('tests/test_g1_3_wan_off_scope_replay.py', 'w') as f:
    f.write(code)
