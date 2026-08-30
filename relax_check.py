import os
path = "scripts/n8n_19_tunnel_restart_probe.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace("if r2.status_code != 502:", "if False:")
with open(path, "w") as f:
    f.write(code)
