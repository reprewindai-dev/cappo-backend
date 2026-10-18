# ruff: noqa
import json
from enum import Enum
from typing import Optional

from .errors import *
from .models import (
    AuthorityArtifact,
    ExecutionContextToken,
    WorkloadIdentityToken,
    WorkloadProofToken,
)
from .validator import IdentityValidator


class RouteClassification(Enum):
    PUBLIC = "public"
    GOVERNED = "governed"
    STATE_CHANGING = "state_changing"
    CONSEQUENCE = "consequence"

class WIDMiddlewareContext:
    def __init__(self, classification: RouteClassification, validator: IdentityValidator):
        self.classification = classification
        self.validator = validator
        
    def enforce(
        self,
        route: str,
        method: str,
        trace_id: str,
        htu: str,
        body_hash: str,
        wit_payload: Optional[dict] = None,
        ect_payload: Optional[dict] = None,
        wpt_payload: Optional[dict] = None,
        authority_payload: Optional[dict] = None,
        lease_execution_id: Optional[str] = None,
    ):
        """Enforce workload identity for the route's classification.

        A CONSEQUENCE route needs authority. ``lease_execution_id`` is that authority
        when the caller resolved it server-side from an active capability lease; it is
        never read from the request. The Execution-Context must then name the leased
        execution, so identity tokens minted for one execution cannot drive another.
        """
        if self.classification == RouteClassification.PUBLIC:
            return
            
        # Governed requirements
        if not wit_payload:
            raise MissingWorkloadIdentityError(route=route, method=method, trace_id=trace_id)
        if not ect_payload:
            raise MissingExecutionContextError(route=route, method=method, trace_id=trace_id)
            
        wit = WorkloadIdentityToken(**wit_payload)
        ect = ExecutionContextToken(**ect_payload)
        
        self.validator.validate_wit(wit, route, method, trace_id)
        self.validator.validate_ect(ect, route, method, trace_id)
        
        if self.classification == RouteClassification.GOVERNED:
            return
            
        # State-Changing requirements
        if not wpt_payload:
            raise MissingWorkloadProofError(route=route, method=method, trace_id=trace_id, workload_identifier=wit.sub, ephemeral_execution_id=ect.ephemeral_execution_id)
            
        wpt = WorkloadProofToken(**wpt_payload)
        
        # In a real system, we compute sha256(authority_token_raw).
        import hashlib
        import json
        if authority_payload:
            auth_bytes = json.dumps(authority_payload, sort_keys=True).encode()
            expected_auth_hash = hashlib.sha256(auth_bytes).hexdigest()
        else:
            expected_auth_hash = None
            
        wit_bytes = json.dumps(wit_payload, sort_keys=True).encode() if wit_payload else b''
        expected_wit_hash = hashlib.sha256(wit_bytes).hexdigest() if wit_payload else wpt.wit_hash
        
        ect_bytes = json.dumps(ect_payload, sort_keys=True).encode() if ect_payload else b''
        expected_ect_hash = hashlib.sha256(ect_bytes).hexdigest() if ect_payload else wpt.ect_hash
        
        self.validator.validate_wpt(
            wpt=wpt,
            expected_method=method,
            expected_htu=htu,
            expected_body_hash=body_hash,
            expected_wit_hash=expected_wit_hash,
            expected_ect_hash=expected_ect_hash,
            expected_authority_hash=expected_auth_hash,
            route=route,
            trace_id=trace_id
        )
        
        if self.classification == RouteClassification.STATE_CHANGING:
            return
            
        # Consequence requirements
        if lease_execution_id:
            if ect.ephemeral_execution_id != lease_execution_id:
                raise ExecutionContextLeaseMismatchError(route=route, method=method, trace_id=trace_id, workload_identifier=wit.sub, ephemeral_execution_id=ect.ephemeral_execution_id)
            return

        if not authority_payload:
            raise MissingAuthorityError(route=route, method=method, trace_id=trace_id, workload_identifier=wit.sub, ephemeral_execution_id=ect.ephemeral_execution_id)
            
        auth_kwargs = dict(authority_payload)
        if "_mock_hash" in auth_kwargs:
            del auth_kwargs["_mock_hash"]
        authority = AuthorityArtifact(**auth_kwargs)
        self.validator.validate_authority(authority, ect, route, method, trace_id)
