import os
path = "cappo_backend/execution/sandbox_file_connector.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace(
    'print("EXPECTED RESOURCE:", repr(self.resource))',
    'with open("scratch/resource_mismatch.txt", "w") as f:\n                f.write(f"EXPECTED: {repr(self.resource)}\\nPROVIDED: {repr(allowed_resources)}\\n")'
)
with open(path, "w") as f:
    f.write(code)
