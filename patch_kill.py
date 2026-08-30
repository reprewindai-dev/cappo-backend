import os
path = "scripts/n8n_19_tunnel_restart_probe.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace(
    'proc.terminate()\n    proc.wait()',
    'os.system("taskkill /F /IM cloudflared.exe")\n    time.sleep(3)'
)
with open(path, "w") as f:
    f.write(code)
