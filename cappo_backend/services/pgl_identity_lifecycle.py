"""PGL Identity Lifecycle — Probation, Trust Levels, and Annual Renewal.

Shared from the veklom-byos-backend design.
Cappo uses this to stamp agent birth certificates with lifecycle metadata.

KEY DESIGN:
- Same ID for life. Never re-issued. Only renewed.
- Annual renewal = same certificate_id, new expiry in provenance_json
- Probationary 90 days = can be terminated without cause
- ACTIVE after 90 days = full trust, needs formal reason to terminate
- EXPIRED after 365 days without renewal = hard block at gate
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

logger = logging.getLogger(__name__)

# Constants
PROBATION_DAYS        = 90
RENEWAL_INTERVAL_DAYS = 365
RENEWAL_WARNING_DAYS  = 30
GRACE_PERIOD_DAYS     = 14   # 14-day grace period after expiry — agents still run


class TrustLevel(str, Enum):
    PROBATIONARY = "PROBATIONARY"   # < 90 days — can be terminated without cause
    ACTIVE        = "ACTIVE"        # >= 90 days clean — full trust, needs cause to terminate
    RENEWAL_DUE   = "RENEWAL_DUE"   # Within 30 days of renewal deadline — warn + allow
    GRACE_PERIOD  = "GRACE_PERIOD"  # 1-14 days past deadline — daily reminders, still runs
    HARD_EXPIRED  = "HARD_EXPIRED"  # > 14 days past deadline — hard block until renewed


class LifecycleStatus:
    def __init__(
        self,
        trust_level:        TrustLevel,
        probation_ends_at:  datetime,
        renewal_due_at:     datetime,
        days_in_service:    int,
        days_until_renewal: int,
        can_execute:        bool,
        warning:            str | None = None,
        grace_day:          int | None = None,  # 1-14 during GRACE_PERIOD
        hard_block_date:    datetime | None = None,  # when hard block fires
    ) -> None:
        self.trust_level        = trust_level
        self.probation_ends_at  = probation_ends_at
        self.renewal_due_at     = renewal_due_at
        self.days_in_service    = days_in_service
        self.days_until_renewal = days_until_renewal
        self.can_execute        = can_execute
        self.warning            = warning
        self.grace_day          = grace_day
        self.hard_block_date    = hard_block_date

    def to_dict(self) -> dict:
        return {
            "trust_level":        self.trust_level.value,
            "probation_ends_at":  self.probation_ends_at.isoformat(),
            "renewal_due_at":     self.renewal_due_at.isoformat(),
            "days_in_service":    self.days_in_service,
            "days_until_renewal": self.days_until_renewal,
            "can_execute":        self.can_execute,
            "warning":            self.warning,
            "grace_day":          self.grace_day,
            "hard_block_date":    self.hard_block_date.isoformat() if self.hard_block_date else None,
        }


def compute_lifecycle(provenance: dict, created_at: datetime) -> LifecycleStatus:
    """Compute lifecycle from provenance_json and creation date. Pure function."""
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    days_in_service   = (now - created_at).days
    probation_ends_at = created_at + timedelta(days=PROBATION_DAYS)

    last_renewed_raw = provenance.get("last_renewed_at")
    if last_renewed_raw:
        try:
            last_renewed = datetime.fromisoformat(last_renewed_raw)
            if last_renewed.tzinfo is None:
                last_renewed = last_renewed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            last_renewed = created_at
    else:
        last_renewed = created_at

    renewal_due_at     = last_renewed + timedelta(days=RENEWAL_INTERVAL_DAYS)
    days_until_renewal = (renewal_due_at - now).days
    hard_block_date    = renewal_due_at + timedelta(days=GRACE_PERIOD_DAYS)

    if now > hard_block_date:
        return LifecycleStatus(
            trust_level=TrustLevel.HARD_EXPIRED,
            probation_ends_at=probation_ends_at,
            renewal_due_at=renewal_due_at,
            days_in_service=days_in_service,
            days_until_renewal=days_until_renewal,
            can_execute=False,
            hard_block_date=hard_block_date,
            warning=(
                f"PGL identity HARD EXPIRED on {hard_block_date.date()}. "
                f"14-day grace period has ended. "
                f"POST /api/v1/agents/renew to restore execution immediately. "
                f"Same ID, new expiry — no re-registration needed."
            ),
        )

    if now > renewal_due_at:
        grace_day = (now - renewal_due_at).days + 1
        days_remaining = (hard_block_date - now).days
        return LifecycleStatus(
            trust_level=TrustLevel.GRACE_PERIOD,
            probation_ends_at=probation_ends_at,
            renewal_due_at=renewal_due_at,
            days_in_service=days_in_service,
            days_until_renewal=days_until_renewal,
            can_execute=True,   # Grace period: still runs!
            grace_day=grace_day,
            hard_block_date=hard_block_date,
            warning=(
                f"GRACE PERIOD — Day {grace_day} of {GRACE_PERIOD_DAYS}. "
                f"{days_remaining} day{'s' if days_remaining != 1 else ''} until hard block "
                f"({hard_block_date.date()}). "
                f"Renew NOW at POST /api/v1/agents/renew. "
                f"Your agents are still running."
            ),
        )

    if days_until_renewal <= RENEWAL_WARNING_DAYS:
        return LifecycleStatus(
            trust_level=TrustLevel.RENEWAL_DUE,
            probation_ends_at=probation_ends_at,
            renewal_due_at=renewal_due_at,
            days_in_service=days_in_service,
            days_until_renewal=days_until_renewal,
            can_execute=True,
            warning=(
                f"Renewal due in {days_until_renewal} days ({renewal_due_at.date()}). "
                f"Renew before deadline or execution will be blocked."
            ),
        )

    if now < probation_ends_at:
        days_left = (probation_ends_at - now).days
        return LifecycleStatus(
            trust_level=TrustLevel.PROBATIONARY,
            probation_ends_at=probation_ends_at,
            renewal_due_at=renewal_due_at,
            days_in_service=days_in_service,
            days_until_renewal=days_until_renewal,
            can_execute=True,
            warning=(
                f"PROBATIONARY — {days_left} days until full trust "
                f"(probation ends {probation_ends_at.date()}). "
                f"Can be terminated without cause during probation."
            ),
        )

    return LifecycleStatus(
        trust_level=TrustLevel.ACTIVE,
        probation_ends_at=probation_ends_at,
        renewal_due_at=renewal_due_at,
        days_in_service=days_in_service,
        days_until_renewal=days_until_renewal,
        can_execute=True,
        warning=None,
    )


def stamp_agent_provenance(
    agent_id:     str,
    agent_name:   str,
    creator:      str,      # human operator's ID — the chain root
    workspace_id: str,
) -> dict:
    """Build lifecycle-stamped provenance_json for a new PGLCertificate.

    creator is the human operator who owns this agent. This is what links
    every agent back to a human. The chain: human -> agent -> all actions.
    """
    now               = datetime.now(timezone.utc)
    probation_ends_at = now + timedelta(days=PROBATION_DAYS)
    renewal_due_at    = now + timedelta(days=RENEWAL_INTERVAL_DAYS)

    return {
        "agent_id":          agent_id,
        "agent_name":        agent_name,
        "creator":           creator,          # human anchor
        "workspace_id":      workspace_id,
        "kind":              "AGENT",
        "trust_level":       TrustLevel.PROBATIONARY.value,
        "status":            "ACTIVE",
        "probation_ends_at": probation_ends_at.isoformat(),
        "renewal_due_at":    renewal_due_at.isoformat(),
        "last_renewed_at":   None,
        "renewal_count":     0,
        "created_at":        now.isoformat(),
        "identity_version":  1,
        "source":            "cappo_agent_registration",
    }


def build_renewal_patch(current_provenance: dict) -> dict:
    """Build the provenance update for renewal. Same cert_id, new expiry."""
    now           = datetime.now(timezone.utc)
    renewal_count = current_provenance.get("renewal_count", 0) + 1
    new_due       = now + timedelta(days=RENEWAL_INTERVAL_DAYS)

    return {
        **current_provenance,
        "last_renewed_at":  now.isoformat(),
        "renewal_due_at":   new_due.isoformat(),
        "renewal_count":    renewal_count,
        "identity_version": current_provenance.get("identity_version", 1) + 1,
    }
