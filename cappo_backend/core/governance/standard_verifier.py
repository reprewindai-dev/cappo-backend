import os
from typing import Any, Dict, List

import yaml


class StandardVerifier:
    def __init__(self, standards_dir: str = None):
        if standards_dir is None:
            # Default to the standards directory relative to this file
            self.standards_dir = os.path.join(os.path.dirname(__file__), "standards")
        else:
            self.standards_dir = standards_dir

        self.standards = self._load_standards()

    def _load_standards(self) -> Dict[str, Any]:
        """Loads all standard definitions from the standards directory."""
        standards = {}
        if not os.path.exists(self.standards_dir):
            return standards

        for filename in os.listdir(self.standards_dir):
            if filename.endswith((".yaml", ".yml")):
                filepath = os.path.join(self.standards_dir, filename)
                try:
                    with open(filepath, "r") as f:
                        data = yaml.safe_load(f)
                        if data and "standard" in data:
                            standards[data["standard"]] = data
                except Exception as e:
                    print(f"Error loading standard {filename}: {e}")
        return standards

    def verify(self, required_standards: List[str], execution_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Verifies the execution context against the required standards by evaluating
        the field_check requirements defined in each standard's YAML definition.

        Structural field checks may produce PASS/FAIL only for standards whose
        verification contract is actually implemented here. Protocols requiring an
        external cryptographic verifier must remain NOT_VERIFIED until that verifier
        is integrated; caller-supplied presence flags are not verification evidence.
        """
        results = []
        for std_id in required_standards:
            if std_id not in self.standards:
                results.append({
                    "id": std_id,
                    "result": "NOT_FOUND",
                    "reason": f"Standard '{std_id}' is not defined in the trust spine."
                })
                continue

            std_def = self.standards[std_id]
            verification = std_def.get("verification", {})

            # x402 requires cryptographically verified settlement evidence. The
            # current StandardVerifier has no settlement verifier, key provenance,
            # replay protection, or durable receipt lookup. HTTP 402 plus a
            # caller-supplied `has_receipt_id` flag can therefore never establish
            # x402 compliance. Keep this fail-closed until the canonical settlement
            # verifier is wired in.
            if std_id == "x402" and verification.get("evidence_required", False):
                results.append({
                    "id": std_id,
                    "version": str(std_def.get("version", "unknown")),
                    "result": "NOT_VERIFIED",
                    "reason": (
                        "x402 cryptographic settlement verification is not integrated; "
                        "HTTP/header presence checks are insufficient evidence."
                    )
                })
                continue

            requirements = std_def.get("requirements", [])

            if not requirements:
                results.append({
                    "id": std_id,
                    "version": str(std_def.get("version", "unknown")),
                    "result": "NOT_EVALUATED",
                    "reason": "No requirements defined for this standard in the trust spine."
                })
                continue

            failed_requirements = []
            for req in requirements:
                req_id = req.get("id", "unknown_req")
                req_type = req.get("type", "MUST")
                field = req.get("field_check")
                expected_value = req.get("expected_value")
                required = req.get("required", True)

                if not field:
                    if req_type == "MUST":
                        failed_requirements.append(
                            f"Mandatory requirement '{req_id}' is missing a field_check and cannot be evaluated."
                        )
                    continue

                actual = execution_context.get(field)

                if actual is None:
                    if required:
                        failed_requirements.append(
                            f"Required field '{field}' is absent from the execution context."
                        )
                    # Optional and absent — not a failure.
                elif expected_value is not None and actual != expected_value:
                    failed_requirements.append(
                        f"Field '{field}' = '{actual}'; expected '{expected_value}'."
                    )
                # expected_value is None and field is present → presence-only check passes.

            if failed_requirements:
                results.append({
                    "id": std_id,
                    "version": str(std_def.get("version", "unknown")),
                    "result": "FAIL",
                    "failures": failed_requirements
                })
            else:
                results.append({
                    "id": std_id,
                    "version": str(std_def.get("version", "unknown")),
                    "result": "PASS"
                })

        return results