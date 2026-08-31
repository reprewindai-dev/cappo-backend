import time
from typing import Optional

from cappo_backend.identity.models import (
    AuthorityArtifact,
    ExecutionContextToken,
    WorkloadIdentityToken,
    WorkloadProofToken,
)

from .errors import (
    ApiKeyOnlyDeniedError,
    AuthorityEiMismatchError,
    AuthorityExpiredError,
    AuthorityHashMismatchError,
    CandidateActMismatchError,
    ClaimedRoleDeniedError,
    DestinationHashMismatchError,
    MissingEphemeralExecutionIdError,
    OperatorAssertionDeniedError,
    PolicyDecisionHashMismatchError,
    ProfileOnlyDeniedError,
    ReplayDeniedError,
    RightNotGrantedError,
    ScopeHashMismatchError,
    SourceIpOnlyDeniedError,
    StaticServiceDeniedError,
    TenantIdOnlyDeniedError,
    WorkloadIdentifierOnlyDeniedError,
)


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
        profile_id_only: bool = False,
        api_key_only: bool = False,
        static_service_name_only: bool = False,
        claimed_role_only: bool = False,
        source_ip_only: bool = False,
        operator_assertion_only: bool = False,
        tenant_id_only: bool = False,
        workload_identifier_only: bool = False,
        expected_authority_hash: str = "",
        expected_ect_hash: str = "",
        expected_wit_hash: str = "",
        expected_scope_hash: str = "",
        expected_policy_decision_hash: str = "",
        replay_already_checked: bool = False,
    ):
        base_kwargs = {
            "route": route,
            "method": method,
            "trace_id": trace_id,
            "workload_identifier": wit.sub if wit else None,
            "ephemeral_execution_id": ect.ephemeral_execution_id if ect else None,
            "candidate_act_hash": ect.candidate_act_hash if ect else None,
            "authority_id": authority.authority_id if authority else None,
        }

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

        if not wit or not ect or not wpt or not authority:
            raise ProfileOnlyDeniedError(**base_kwargs)
        if not authority.ephemeral_execution_id or not ect.ephemeral_execution_id:
            raise MissingEphemeralExecutionIdError(**base_kwargs)

        if authority.ephemeral_execution_id != ect.ephemeral_execution_id:
            raise AuthorityEiMismatchError(**base_kwargs)
        if wit.sub != ect.sub:
            raise CandidateActMismatchError(**base_kwargs)
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
        if authority.expires_at < int(time.time()):
            raise AuthorityExpiredError(**base_kwargs)

        # CONSEQUENCE routes defer request-proof consumption to this complete
        # preauthorization step. Standalone callers retain the same check. Only
        # a caller that already consumed this exact JTI may opt out explicitly.
        if not replay_already_checked:
            if not self.replay_cache.check_and_store(
                wpt.jti,
                wpt.exp if wpt.exp else int(time.time()) + 300,
            ):
                raise ReplayDeniedError(**base_kwargs)

        if requested_right not in authority.rights:
            raise RightNotGrantedError(**base_kwargs)
        if authority.inbound_truth_state != authority.required_truth_state:
            raise RuntimeError(
                "InboundTruthRequirementFailed: context certification state is below required policy"
            )
        return True
