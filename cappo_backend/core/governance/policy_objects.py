from typing import Dict, Any, List

# In a mature system this would be parsed from YAML files, but we define them here as structured dicts
CAPABILITY_POLICIES: List[Dict[str, Any]] = [
    {
        "capability": "blueprint.generate",
        "jurisdiction": "Canada",
        "classification": ["Code", "PII"],
        "enforcement": {
            "action": "ALLOW_WITH_REDACTION"
        },
        "approval": {
            "required": False
        },
        "audit": {
            "evidence": "required"
        },
        "retry": {
            "allowed": True
        },
        "version": "1.2"
    },
    {
        "capability": "financial.transfer",
        "jurisdiction": "Canada",
        "classification": ["Financial", "PII"],
        "enforcement": {
            "action": "FAIL_CLOSED"
        },
        "approval": {
            "required": True
        },
        "audit": {
            "evidence": "required"
        },
        "retry": {
            "allowed": False
        },
        "version": "1.2"
    },
    {
        "capability": "identity.verify",
        "jurisdiction": "*",
        "classification": ["PII", "High-Risk"],
        "enforcement": {
            "action": "FAIL_CLOSED"
        },
        "approval": {
            "required": True
        },
        "audit": {
            "evidence": "required"
        },
        "retry": {
            "allowed": False
        },
        "version": "1.0"
    },
    {
        "capability": "public.search",
        "jurisdiction": "EU",
        "classification": ["Public"],
        "enforcement": {
            "action": "ALLOW_WITH_REDACTION"
        },
        "approval": {
            "required": False
        },
        "audit": {
            "evidence": "required"
        },
        "retry": {
            "allowed": True
        },
        "version": "1.0"
    },
    {
        "capability": "github.issue.create",
        "jurisdiction": "Global",
        "classification": ["Code"],
        "enforcement": {
            "action": "ALLOW_WITH_SECRET_INJECTION"
        },
        "approval": {
            "required": False
        },
        "audit": {
            "evidence": "required"
        },
        "retry": {
            "allowed": True
        },
        "version": "1.0"
    }
]
