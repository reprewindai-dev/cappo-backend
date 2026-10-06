"""Commercial credit metering at the CAPPO governance point.

Credits are accounting; CAPPO is authority. This client only asks
LockerPhycer (the commercial/entitlement plane) whether a governed call may be
charged, *before* CAPPO exercises authority -- the same ordering as the legacy
PaymentGate ("402 precedes LAW 0"). A commercial allow never grants authority,
and a commercial denial never records an authority decision.

Failure policy:
  * paid actions (governed action / execution): fail CLOSED -- if the meter is
    unreachable, misconfigured or the workspace is unknown, the call is denied
    with a structured METERING_UNAVAILABLE (503).
  * verification reads: fail SAFE -- allowed and left unmetered.
If CAPPO denies after a successful charge, the charge is reversed (best effort,
idempotent on the LockerPhycer side).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VERIFICATION_READ = "verification_read"
GOVERNED_ACTION = "standard_governed_action"
GOVERNED_EXECUTION = "governed_execution"
READ_ACTIONS = frozenset({VERIFICATION_READ})


@dataclass
class MeterOutcome:
    allowed: bool
    charged: bool = False
    replay: bool = False
    credits: int = 0
    entry_id: str | None = None
    status_code: int | None = None
    denial: dict[str, Any] | None = None
    unmetered_reason: str | None = None
    balance: dict[str, Any] = field(default_factory=dict)


class EntitlementMeter:
    def __init__(self, settings: Any, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._sent_events: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return str(getattr(self.settings, "entitlements_metering", "off")).lower() == "enforce"

    @property
    def _configured(self) -> bool:
        return bool(self.settings.entitlements_url and self.settings.entitlements_service_token)

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        url = f"{self.settings.entitlements_url.rstrip('/')}/api/v1/internal{path}"
        with httpx.Client(transport=self._transport,
                          timeout=self.settings.entitlements_timeout_ms / 1000.0) as client:
            return client.post(url, json=body,
                               headers={"X-Veklom-Service-Token": self.settings.entitlements_service_token})

    @staticmethod
    def _unavailable(reason: str) -> dict[str, Any]:
        return {"code": "METERING_UNAVAILABLE", "reason": reason,
                "message": "Commercial metering unavailable; paid governed actions fail closed."}

    def charge(self, *, workspace_id: str, action_type: str, idempotency_key: str,
               mount_id: str | None = None, execution_ref: str | None = None,
               operation_ref: str | None = None, principal: str | None = None) -> MeterOutcome:
        if not self.enabled:
            return MeterOutcome(allowed=True, unmetered_reason="metering_off")
        is_read = action_type in READ_ACTIONS

        def fail(reason: str) -> MeterOutcome:
            if is_read:
                logger.warning("verification read left unmetered: %s", reason)
                return MeterOutcome(allowed=True, unmetered_reason=reason)
            return MeterOutcome(allowed=False, status_code=503, denial=self._unavailable(reason))

        if not self._configured:
            return fail("not_configured")
        try:
            resp = self._post("/entitlements/meter", {
                "workspace_id": workspace_id, "action_type": action_type,
                "idempotency_key": idempotency_key, "mount_id": mount_id,
                "execution_ref": execution_ref, "operation_ref": operation_ref,
                "principal": principal,
            })
        except httpx.HTTPError as exc:
            return fail(f"unreachable:{type(exc).__name__}")
        if resp.status_code == 200:
            data = resp.json()
            return MeterOutcome(allowed=bool(data.get("allowed", True)), charged=bool(data.get("charged")),
                                replay=bool(data.get("replay")), credits=int(data.get("credits") or 0),
                                entry_id=data.get("entry_id"), balance=data.get("balance") or {})
        if resp.status_code in (402, 403, 429):
            try:
                denial = (resp.json() or {}).get("error") or {}
            except ValueError:
                denial = {}
            if is_read:  # never block reads
                return MeterOutcome(allowed=True, unmetered_reason=str(denial.get("code") or resp.status_code))
            return MeterOutcome(allowed=False, status_code=resp.status_code, denial=denial)
        return fail(f"http_{resp.status_code}")

    def reverse(self, idempotency_key: str, reason: str) -> bool:
        if not (self.enabled and self._configured):
            return False
        try:
            resp = self._post("/entitlements/reverse", {"idempotency_key": idempotency_key, "reason": reason[:120]})
            return resp.status_code == 200 and bool(resp.json().get("reversed"))
        except Exception:
            logger.warning("credit reversal failed for %s; reconcile manually", idempotency_key)
            return False

    def emit(self, event_name: str, workspace_id: str | None, *, ref: str | None = None,
             details: dict[str, Any] | None = None, background: bool = True) -> None:
        """Report an activation milestone (fire-and-forget, deduped locally and in LockerPhycer)."""
        if not (self.enabled and self._configured and workspace_id):
            return
        marker = (event_name, workspace_id)
        if event_name != "capability_issued":
            with self._lock:
                if marker in self._sent_events:
                    return
                self._sent_events.add(marker)

        def _send() -> None:
            try:
                self._post("/activation-events", {"event_name": event_name, "workspace_id": workspace_id,
                                                  "ref": ref, "details": details or {}})
            except Exception:
                with self._lock:
                    self._sent_events.discard(marker)
                logger.warning("activation event %s not delivered", event_name)

        if background:
            threading.Thread(target=_send, daemon=True).start()
        else:
            _send()


def get_meter(app_state: Any) -> EntitlementMeter:
    meter = getattr(app_state, "entitlement_meter", None)
    if meter is None:
        meter = EntitlementMeter(app_state.settings)
        app_state.entitlement_meter = meter
    return meter
