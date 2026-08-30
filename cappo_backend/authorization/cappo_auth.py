import time
from typing import Optional, List
from .errors import *
from cappo_backend.identity.models import WorkloadIdentityToken, ExecutionContextToken, WorkloadProofToken, AuthorityArtifact

class CappoPreauthorizationEnforcer:
    def __init__(self, replay_cache):
        self.replay_cache = replay_cache

    def authorize_consequence(
        self,
        route: str,
        method: str,
        trace_id: str,
        request_target_hash: str,
        request_body_hash: str,
        requested_right: str,
        wit: Optional[WorkloadIdentityToken] = None,
        ect: Optional[ExecutionContextToken] = None,
        wpt: Optional[WorkloadProofToken] = None,
        authority: Optional[AuthorityArtifact] = None,
        # Legacy/insufficient signals that must be rejected:
        profile_id_only: bool = False,
        api_key_only: bool = False,
        static_service_name_only: bool = False,
        claimed_role_only: bool = False,
        source_ip_only: bool = False,
        operator_assertion_only: bool = False,
        tenant_id_only: bool = False,
        workload_identifier_only: bool = False,
        # Hashes (in reality these would be calculated, here we pass the expected hashes of the raw tokens for verification)
        expected_authority_hash: str = "",
        expected_ect_hash: str = "",
        expected_wit_hash: str = "",
        expected_scope_hash: str = "",
        expected_policy_decision_hash: str = ""
    ):
        base_kwargs = {
            "route": route,
            "method": method,
            "trace_id": trace_id,
            "workload_identifier": wit.sub if wit else None,
            "ephemeral_execution_id": ect.ephemeral_execution_id if ect else None,
            "candidate_act_hash": ect.candidate_act_hash if ect else None,
            "authority_id": authority.authority_id if authority else None
        }

        # 1. Reject insufficient authority vectors
        if profile_id_only:
            raise ProfileOnlyDeniedError(**base_kwargs)
        if api_key_only:
            raise ApiKeyOnlyDeniedError(**base_kwargs)
        if static_service_name_only:
            raise StaticServiceDeniedError(**base_kwargs)
        if claimed_role_only:
            raise ClaimedRoleDeniedError(**base_kwargs)
        if source_ip_only:
            raise SourceIpOnlyDeniedError(**base_kwargs)
        if operator_assertion_only:
            raise OperatorAssertionDeniedError(**base_kwargs)
        if tenant_id_only:
            raise TenantIdOnlyDeniedError(**base_kwargs)
        if workload_identifier_only:
            raise WorkloadIdentifierOnlyDeniedError(**base_kwargs)

        # 2. Require complete proof chain
        if not wit or not ect or not wpt or not authority:
            raise ProfileOnlyDeniedError(**base_kwargs)  # or a missing error, but WID-3 asks to prove CAPPO refuses these

        if not authority.ephemeral_execution_id or not ect.ephemeral_execution_id:
            raise MissingEphemeralExecutionIdError(**base_kwargs)

        # 3. Cryptographic and Identity Bindings
        if authority.ephemeral_execution_id != ect.ephemeral_execution_id:
            raise AuthorityEiMismatchError(**base_kwargs)
            
        if wit.sub != ect.sub: # assuming sub is workload_identifier
            raise CandidateActMismatchError(**base_kwargs) # using this or another error if mismatched

        if authority.candidate_act_hash != ect.candidate_act_hash:
            raise CandidateActMismatchError(**base_kwargs)

        if wpt.authority_hash != expected_authority_hash:
            raise AuthorityHashMismatchError(**base_kwargs)

        if wpt.ect_hash != expected_ect_hash:
            raise AuthorityHashMismatchError(**base_kwargs)

        if wpt.wit_hash != expected_wit_hash:
            raise AuthorityHashMismatchError(**base_kwargs)

        if wpt.body_hash != request_body_hash:
            raise AuthorityHashMismatchError(**base_kwargs)

        if authority.scope_hash != expected_scope_hash:
            raise ScopeHashMismatchError(**base_kwargs)

        if authority.destination_hash != request_target_hash:
            raise DestinationHashMismatchError(**base_kwargs)

        if authority.policy_decision_hash != expected_policy_decision_hash:
            raise PolicyDecisionHashMismatchError(**base_kwargs)

        # 4. TTL
        if authority.expires_at < int(time.time()):
            raise AuthorityExpiredError(**base_kwargs)

        # 5. Replay Denial (checking authority JTI or WPT JTI)
        # Using WPT JTI for request-level replay prevention
        if not self.replay_cache.check_and_store(wpt.jti, wpt.exp if wpt.exp else int(time.time()) + 300):
            raise ReplayDeniedError(**base_kwargs)

        # 6. Rights Validation
        if requested_right not in authority.rights:
            raise RightNotGrantedError(**base_kwargs)

        if "truth.transition" == requested_right and "truth.transition" not in authority.rights:
            raise RightNotGrantedError(**base_kwargs)

        if authority and getattr(authority, 'inbound_truth_state', 'ADMISSIBLE') != getattr(authority, 'required_truth_state', 'ADMISSIBLE'):
            raise Exception('InboundTruthRequirementFailed: Context certification state is below required policy.')

        # All checks passed, CAPPO preauthorization successful.
        return True
