from sqlalchemy.orm import Session
from fastapi import HTTPException
from cappo_backend.identity.models import AuthorityArtifact, WorkloadIdentityToken, ExecutionContextToken, WorkloadProofToken

class LegacyCredentialAdapter:
    def __init__(self, db: Session):
        self.db = db

    def translate(
        self,
        auth: AuthorityArtifact,
        wit: WorkloadIdentityToken | None,
        ect: ExecutionContextToken | None,
        wpt: WorkloadProofToken | None,
    ):
        # For this architectural boundary, we reject if we cannot translate securely.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "LEGACY_CREDENTIALS_UNSUPPORTED",
                "detail": "Legacy credentials cannot be safely translated into a canonical capability lease yet."
            }
        )
