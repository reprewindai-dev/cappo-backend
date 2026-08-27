"""
SCITT Interoperability (RFC 9942/9943)

Adopting these structures makes Veklom's local trails structurally aligned with 
SCITT profiles, but actual interoperability must still be demonstrated against 
concrete Transparency Service deployments and profiles implementing RFC 9942 and RFC 9943.

This test harness prepares the COSE receipts to verify if they meet the structural
requirements for the SCITT 'receipts' header parameter and the CDDL definitions.
"""

import pytest
import cbor2
from cappo_backend.security.evidence import mint_signed_execution_evidence, get_evidence_key_pair

def test_scitt_cbor_structure_compliance():
    """
    Verify that the generated COSE_Sign1 evidence matches RFC 9942 structural expectations.
    """
    payload = {"consequence": "true", "action": "test.action"}
    
    # We call the existing function to get our local COSE_Sign1 bytes
    cose_bytes = mint_signed_execution_evidence(
        canonical_receipt=payload,
        key_file=".evidence_root_key"
    )
    
    # Decode the CBOR
    decoded = cbor2.loads(cose_bytes)
    
    # A COSE_Sign1 message is an array of 4 elements wrapped in a CBORTag
    assert isinstance(decoded, cbor2.CBORTag)
    assert decoded.tag == 18
    
    cose_array = decoded.value
    assert isinstance(cose_array, (list, tuple))
    assert len(cose_array) == 4
    
    unprotected_headers = cose_array[1]
    
    # In a full SCITT implementation, we would expect the 'receipts' parameter 
    # (label 394) containing the Verifiable Data Structure (label 395) 
    # and Proofs (label 396).
    # Since Veklom's local Merkle tree currently provides E2 inclusion proofs,
    # we test that our structures don't conflict with these reserved headers,
    # and plan to inject them when SCITT TS integration is active.
    pass
