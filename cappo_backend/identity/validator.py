import re
import time

from cappo_backend.identity.errors import (
    AudienceMismatchError,
    AuthorityHashMismatchError,
    BodyHashMismatchError,
    CandidateActMismatchError,
    MalformedWorkloadIdentifierError,
    ProfileOnlyDeniedError,
    ReplayDeniedError,
    RequestBindingMismatchError,
    TokenExpiredError,
)
from cappo_backend.identity.models import (
    AuthorityArtifact,
    ExecutionContextToken,
    WorkloadIdentityToken,
    WorkloadProofToken,
)
from cappo_backend.identity.replay_cache import ReplayCache


class IdentityValidator:
    def __init__(self, expected_audience: str, replay_cache: ReplayCache):
        self.expected_audience = expected_audience
        self.replay_cache = replay_cache
        self.wimse_pattern = re.compile(
            r"^wimse://[a-zA-Z0-9.-]+/[a-zA-Z0-9.-]+/[a-zA-Z0-9.-]+/[a-zA-Z0-9.-]+/[a-zA-Z0-9.-]+$"
        )

    def validate_wit(
        self,
        wit: WorkloadIdentityToken,
        route: str,
        method: str,
        trace_id: str,
    ) -> None:
        now = int(time.time())
        if wit.exp < now:
            raise TokenExpiredError(
                route=route,
                method=method,
                trace_id=trace_id,
                workload_identifier=wit.sub,
            )
        if wit.aud != self.expected_audience:
            raise AudienceMismatchError(
                route=route,
                method=method,
                trace_id=trace_id,
                workload_identifier=wit.sub,
            )
        if not self.wimse_pattern.match(wit.sub):
            raise MalformedWorkloadIdentifierError(
                route=route,
                method=method,
                trace_id=trace_id,
                workload_identifier=wit.sub,
            )

        # Token classes have independent replay namespaces.  A coincidentally equal
        # WIT/WPT identifier must not collide, while replaying the same WIT must.
        if not self.replay_cache.check_and_store(f"wit:{wit.jti}", wit.exp):
            raise ReplayDeniedError(
                route=route,
                method=method,
                trace_id=trace_id,
                workload_identifier=wit.sub,
            )

    def validate_ect(
        self,
        ect: ExecutionContextToken,
        route: str,
        method: str,
        trace_id: str,
    ) -> None:
        now = int(time.time())
        if ect.exp < now:
            raise TokenExpiredError(
                route=route,
                method=method,
                trace_id=trace_id,
                ephemeral_execution_id=ect.ephemeral_execution_id,
            )
        if ect.aud != self.expected_audience:
            raise AudienceMismatchError(
                route=route,
                method=method,
                trace_id=trace_id,
                ephemeral_execution_id=ect.ephemeral_execution_id,
            )

    def validate_wpt(
        self,
        wpt: WorkloadProofToken,
        expected_method: str,
        expected_htu: str,
        expected_body_hash: str,
        expected_wit_hash: str,
        expected_ect_hash: str,
        expected_authority_hash: str,
        route: str,
        trace_id: str,
    ) -> None:
        if wpt.exp and wpt.exp < int(time.time()):
            raise TokenExpiredError(route=route, method=expected_method, trace_id=trace_id)
        if wpt.htm != expected_method or wpt.htu != expected_htu:
            raise RequestBindingMismatchError(
                route=route,
                method=expected_method,
                trace_id=trace_id,
            )
        if wpt.body_hash != expected_body_hash:
            raise BodyHashMismatchError(
                route=route,
                method=expected_method,
                trace_id=trace_id,
            )
        if expected_authority_hash and wpt.authority_hash != expected_authority_hash:
            raise AuthorityHashMismatchError(
                route=route,
                method=expected_method,
                trace_id=trace_id,
            )

        # WPT replay is consumed exactly once here. CAPPO preauthorization consumes
        # the authority artifact in its own namespace instead of consuming WPT twice.
        cache_exp = wpt.exp if wpt.exp else int(time.time()) + 300
        if not self.replay_cache.check_and_store(f"wpt:{wpt.jti}", cache_exp):
            raise ReplayDeniedError(
                route=route,
                method=expected_method,
                trace_id=trace_id,
            )

    def validate_authority(
        self,
        authority: AuthorityArtifact,
        ect: ExecutionContextToken,
        route: str,
        method: str,
        trace_id: str,
    ) -> None:
        now = int(time.time())
        if authority.expires_at < now:
            raise TokenExpiredError(
                route=route,
                method=method,
                trace_id=trace_id,
                ephemeral_execution_id=authority.ephemeral_execution_id,
            )
        if not authority.ephemeral_execution_id:
            raise ProfileOnlyDeniedError(route=route, method=method, trace_id=trace_id)
        if authority.ephemeral_execution_id != ect.ephemeral_execution_id:
            raise CandidateActMismatchError(
                route=route,
                method=method,
                trace_id=trace_id,
                ephemeral_execution_id=ect.ephemeral_execution_id,
            )
        if authority.candidate_act_hash != ect.candidate_act_hash:
            raise CandidateActMismatchError(
                route=route,
                method=method,
                trace_id=trace_id,
                ephemeral_execution_id=ect.ephemeral_execution_id,
            )
