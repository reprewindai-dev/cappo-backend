"""bring the VNP schema under migration control

Revision ID: b7d1e4f6a902
Revises: 9978153219c2
"""

from typing import Sequence, Union

from alembic import op

from cappo_backend.models.vnp_models import (
    APIState,
    ComplianceAuditLog,
    PerformanceLeaderboard,
    ProbeEvent,
    RegionalTelemetry,
    RouteSnapshot,
    VNPIncident,
    VNPProvider,
    VNPSDKCredential,
    VNPTransaction,
    VNPUser,
    VNPValidator,
)

revision: str = "b7d1e4f6a902"
down_revision: Union[str, None] = "9978153219c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VNP_TABLES = (
    VNPUser.__table__,
    VNPProvider.__table__,
    APIState.__table__,
    ProbeEvent.__table__,
    RouteSnapshot.__table__,
    VNPSDKCredential.__table__,
    VNPValidator.__table__,
    VNPIncident.__table__,
    RegionalTelemetry.__table__,
    VNPTransaction.__table__,
    PerformanceLeaderboard.__table__,
    ComplianceAuditLog.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _VNP_TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for table in reversed(_VNP_TABLES):
        table.drop(bind=op.get_bind(), checkfirst=True)
