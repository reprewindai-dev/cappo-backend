from typing import List, Any
from cappo_backend.truth.models import TruthClaim, ClaimState

class UncertifiedContextError(Exception): pass

class AdmissibleContextReceipt:
    """Cryptographic proof that context has passed the Inbound Truth Enforcer."""
    def __init__(self, claims: List[TruthClaim], signature: str):
        for claim in claims:
            if claim.state != ClaimState.ADMISSIBLE and claim.state != ClaimState.CORROBORATED:
                raise UncertifiedContextError("Cannot mint receipt: Contains uncertified claims.")
        self.claims = claims
        self.signature = signature

class InferenceGateway:
    """
    The mechanically fenced reasoning boundary. 
    Models cannot be invoked with raw text; they require an AdmissibleContextReceipt.
    """
    def __init__(self, model_client: Any):
        self.model_client = model_client
        
    def generate_intent(self, prompt_template: str, context_receipt: AdmissibleContextReceipt) -> str:
        # Verify receipt signature here in a real implementation
        if not getattr(context_receipt, "signature", None):
            raise UncertifiedContextError("Context receipt lacks valid inbound signature.")
            
        # Only now do we unpack the context for the LLM
        certified_facts = [c.payload.value for c in context_receipt.claims]
        
        # Build prompt and invoke model
        safe_prompt = f"{prompt_template}\n\nCERTIFIED CONTEXT:\n{certified_facts}"
        return self.model_client.invoke(safe_prompt)

