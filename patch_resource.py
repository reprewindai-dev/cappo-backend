import os
path = "scripts/n8n_19_tunnel_restart_probe.py"
with open(path, "r") as f:
    code = f.read()

# Add import if missing
if "SandboxFileAppendConnector" not in code:
    code = code.replace("from cryptography.hazmat.primitives.asymmetric import ed25519", "from cryptography.hazmat.primitives.asymmetric import ed25519\nfrom cappo_backend.execution.sandbox_file_connector import SandboxFileAppendConnector")

code = code.replace("resource = f'sandbox-file:{TARGET_PATH.as_posix()}'", "resource = SandboxFileAppendConnector.canonicalize_resource(TARGET_PATH)")

with open(path, "w") as f:
    f.write(code)
