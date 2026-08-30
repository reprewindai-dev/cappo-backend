import os
path = "scripts/n8n_17_connector_target.py"
with open(path, "r") as f: code = f.read()

replacement = """class CappoPublicKeyFetcher:
    def __init__(self, base_url: str, internal_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token
        self._cache = {}

    def __call__(self, kid: str) -> bytes | None:
        if kid in self._cache: return self._cache[kid]
        headers = {"Authorization": f"Bearer {self.internal_token}"} if self.internal_token else {}
        try:
            response = httpx.get(f"{self.base_url}/api/v1/execution/keys/{kid}", headers=headers, timeout=3.0)
            if response.status_code == 404: return None
            response.raise_for_status()
            encoded = response.json()["public_key"]
            pub_bytes = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            self._cache[kid] = pub_bytes
            return pub_bytes
        except httpx.ConnectError:
            # If CAPPO is offline, we must fail if not cached
            raise
"""

code = code.replace("class CappoPublicKeyFetcher:\n    def __init__(self, base_url: str, internal_token: str | None = None) -> None:\n        self.base_url = base_url.rstrip(\"/\")\n        self.internal_token = internal_token\n\n    def __call__(self, kid: str) -> bytes | None:\n        headers = {\"Authorization\": f\"Bearer {self.internal_token}\"} if self.internal_token else {}\n        response = httpx.get(\n            f\"{self.base_url}/api/v1/execution/keys/{kid}\", headers=headers, timeout=3.0\n        )\n        if response.status_code == 404:\n            return None\n        response.raise_for_status()\n        encoded = response.json()[\"public_key\"]\n        return base64.urlsafe_b64decode(encoded + \"=\" * (-len(encoded) % 4))", replacement)

with open(path, "w") as f: f.write(code)
