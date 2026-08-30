import os
path = "scripts/n8n_17_connector_target.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace(
    'logger.warning("Authority verification failed: %s", type(exc).__name__)',
    'logger.exception("Authority verification failed")'
)
with open(path, "w") as f:
    f.write(code)
