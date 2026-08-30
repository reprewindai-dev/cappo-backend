import os
path = "scripts/n8n_19_tunnel_restart_probe.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace(
    'print(f"FAILED: n8n.veklom.com still up.")',
    'print(f"FAILED: n8n.veklom.com returned {r2.status_code} {r2.text[:100]}")'
)
with open(path, "w") as f:
    f.write(code)
