import os
path = "scripts/n8n_19_tunnel_restart_probe.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace(
    "stderr=subprocess.DEVNULL)",
    'stderr=open("scratch/cloudflared_err.log", "w"))'
)
with open(path, "w") as f:
    f.write(code)
