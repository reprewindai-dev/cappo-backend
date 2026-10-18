"""HTTP target adapter: dispatch a permitted consequence to an external target.

CAPPO sends the exact request body with ``X-Veklom-Permit``: a permit minted for
those exact bytes (``ConsequenceContext.permit_for``). The target redeems it with
CAPPO (``POST /v1/capability/redeem``) before committing, so authority is checked
at the target as well as here. Built for the arena protected dataset
(``demo-arena``); any target speaking the same /apply contract can be registered.

Outcomes map onto the engine's honest reporting:
  * 201 applied                      -> success
  * 403 / 409 / 400 (target refused) -> TargetRefusedError: nothing committed
  * connection refused before send   -> TargetRefusedError: nothing committed
  * timeout / 5xx / unreadable reply -> CappoUncertainError: OUTCOME_UNKNOWN,
                                        never assumed applied or not applied
Without permits configured the adapter refuses: a target that redeems would
reject an unpermitted write anyway, and sending one would only spend its log.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Iterable

from .effects import CappoUncertainError, ConsequenceContext
from .errors import TargetRefusedError

_OPS = {"record.insert": "insert", "record.modify": "modify", "record.delete": "delete"}


class HttpTargetAdapter:
    """POSTs ``{operation_id, op, record_id, fields}`` to the target's /apply."""

    # Its actions are all writes, but it can still return the target's own public
    # state summary (read_state), so CAPPO's target-state route may call it.
    state_readable = True
    # One dataset, no project scope in the request: a sandbox mount must never reach it.
    project_isolated = False

    def __init__(
        self,
        ref: str,
        apply_url: str,
        actions: Iterable[str] = tuple(_OPS),
        timeout_s: float = 10.0,
        redeem_public_key_hex: str | None = None,
    ) -> None:
        unknown = set(actions) - set(_OPS)
        if unknown:
            raise ValueError(f"unsupported actions for {ref}: {sorted(unknown)}")
        self.ref = ref
        self.apply_url = apply_url
        self.state_url = apply_url.rsplit("/apply", 1)[0] + "/state"
        self.actions = frozenset(actions)
        self.timeout_s = timeout_s
        # The target's Ed25519 public key, pinned in CAPPO configuration. Redemption
        # (and therefore the permit's target binding) only works for a target with one.
        self.redeem_public_key_hex = redeem_public_key_hex
        self.invocation_count = 0

    @staticmethod
    def _record_id(resource: str) -> int:
        if not resource.isdigit() or not 0 < int(resource) < 10**9:
            raise ValueError("invalid_target_resource")
        return int(resource)

    def _body(self, context: ConsequenceContext) -> bytes:
        op = _OPS[context.action]
        doc: dict[str, object] = {
            "operation_id": context.operation_id,
            "op": op,
            "record_id": self._record_id(context.resource),
        }
        if op != "delete":
            doc["fields"] = dict(context.arguments)
        return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def dispatch(self, context: ConsequenceContext) -> object:
        if context.action not in self.actions:
            raise ValueError("target_not_mapped")
        if not context.operation_id:
            raise TargetRefusedError("target_requires_operation_id")
        if context.permit_for is None:
            raise TargetRefusedError("permits_not_configured")
        body = self._body(context)
        request = urllib.request.Request(
            self.apply_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Veklom-Permit": context.permit_for(body)},
        )
        self.invocation_count += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = _json(exc.read())
            if exc.code in (400, 403, 409):
                raise TargetRefusedError(f"target_refused:{detail.get('reason', exc.code)}") from exc
            raise CappoUncertainError(f"target_status_{exc.code}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):
                raise TargetRefusedError("target_unreachable") from exc
            raise CappoUncertainError(f"target_unreachable_after_send:{type(exc.reason).__name__}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise CappoUncertainError("target_timeout") from exc
        answer = _json(raw)
        if status != 201 or answer.get("decision") != "applied":
            raise CappoUncertainError(f"target_unexpected_answer:{status}")
        return answer

    def read_state(self, context: ConsequenceContext) -> object:
        """The target's own public state summary (no consequence recorded).

        A target like the arena publishes no per-record read: its /state is the dataset
        digest, record count and signed log head. That is what is returned, verbatim.
        It is the target's statement, and a proof run must still read the target's
        stored state independently rather than trust CAPPO relaying this.
        """
        self._record_id(context.resource)
        with urllib.request.urlopen(self.state_url, timeout=self.timeout_s) as response:
            return _json(response.read())


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def load_http_targets(raw: str | None) -> list[HttpTargetAdapter]:
    """``CAPPO_HTTP_TARGETS``: JSON list of {"ref", "apply_url", "redeem_public_key_hex", "actions"?}."""
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("CAPPO_HTTP_TARGETS must be a JSON list")
    return [
        HttpTargetAdapter(
            item["ref"],
            item["apply_url"],
            item.get("actions", tuple(_OPS)),
            redeem_public_key_hex=item.get("redeem_public_key_hex"),
        )
        for item in value
    ]
