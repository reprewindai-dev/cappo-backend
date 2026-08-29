from sqlalchemy import Column, String, LargeBinary, Float, Enum as SQLEnum
from cappo_backend.db.base import Base
import enum

class KMSKeyStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"

class KMSKeyRecord(Base):
    __tablename__ = "kms_key_records"
    
    kid = Column(String, primary_key=True, index=True)
    public_bytes = Column(LargeBinary, nullable=False)
    private_bytes = Column(LargeBinary, nullable=True) # Exists in this HSM mockup, normally wouldn't
    status = Column(SQLEnum(KMSKeyStatus), nullable=False, default=KMSKeyStatus.ACTIVE)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=True)
