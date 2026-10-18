"""VNP Validator Service — validator registry and attestations.

Manages validator membership and their contributions to the protocol.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from cappo_backend.models.vnp_models import VNPValidator


class VNPValidatorService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def register_validator(self, name: str, stake_amount: Decimal = Decimal("0.00")) -> VNPValidator:
        validator = VNPValidator(
            name=name,
            did=f"did:vnp:validator:{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}",
            stake_amount=stake_amount,
            status="Active"
        )
        self._db.add(validator)
        self._db.flush()
        return validator

    def update_stake(self, validator_id: uuid.UUID, amount: Decimal) -> VNPValidator:
        validator = self._db.get(VNPValidator, validator_id)
        if validator:
            validator.stake_amount = amount
            self._db.flush()
        return validator
