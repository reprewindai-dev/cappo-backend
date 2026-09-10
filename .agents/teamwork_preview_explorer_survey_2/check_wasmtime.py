import urllib.request
import json

url = "https://pypi.org/pypi/wasmtime/json"
req = urllib.request.urlopen(url)
data = json.loads(req.read().decode())
ver = data["info"]["version"]
print(f"Latest wasmtime version: {ver}")
print("Linux wheels / distributions:")
for f in data["releases"][ver]:
    fname = f["filename"]
    if "linux" in fname or "any" in fname:
        print(f"  {fname} (python_version: {f.get('python_version')})")
