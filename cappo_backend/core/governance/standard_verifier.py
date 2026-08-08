import os
import yaml
from typing import List, Dict, Any


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

        Returns a list of compliance results. Results use only PASS, FAIL, NOT_FOUND,
        or NOT_EVALUATED — never a mock default. Missing or failing mandatory fields
        produce a FAIL result.
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
