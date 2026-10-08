"""Governed compute on owned machines (Private Cloud).

A placed job's worker commits its result only by presenting the job's single-use
mount-holder credential to CAPPO's execute route. CAPPO then dispatches the commit to
the independent results sink below and terminates the mount. The worker must first
claim its start atomically (``requires_start_claim``): no authority, no compute.

Enabled only when a results sink is configured (``FABRIC_RESULT_SINK_URL`` and
``FABRIC_RESULT_SINK_TOKEN``); without one the package is not offered.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from .effects import CappoUncertainError, ConsequenceContext
from .errors import TargetRefusedError
from .models import CapabilityPackage

FABRIC_COMPUTE_JOB_PACKAGE = CapabilityPackage(
    id="compute.job@v1",
    family="fabric",
    title="Governed Compute Job",
    purpose="Commit the verified result of one placed compute job to the results sink.",
    reads=[],
    writes=["job.commit"],
    blocked=[],
    outputs=["job.result"],
    requires_start_claim=True,
)


class ResultSinkAdapter:
    """POSTs one job result to the append-only results sink (bearer token)."""

    ref = "fabric.result-sink"
    actions = frozenset({"job.commit"})
    state_readable = False

    def __init__(self, url: str, token: str, timeout_s: float = 15.0) -> None:
        self.url, self.token, self.timeout_s = url, token, timeout_s
        self.invocation_count = 0

    def dispatch(self, context: ConsequenceContext) -> object:
        if context.action not in self.actions:
            raise ValueError("target_not_mapped")
        a = dict(context.arguments)
        payload = {
            "job_id": a.get("job_id"),
            "op_id": context.operation_id,
            "worker_id": a.get("worker_id"),
            "hostname": a.get("hostname"),
            "result_hash": a.get("result_hash"),
            "elapsed_s": a.get("elapsed_s"),
            "output": a.get("output"),
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        self.invocation_count += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 409):
                raise TargetRefusedError(f"target_refused:{exc.code}") from exc
            raise CappoUncertainError(f"target_status_{exc.code}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):
                raise TargetRefusedError("target_unreachable") from exc
            raise CappoUncertainError(f"target_unreachable_after_send:{type(exc.reason).__name__}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise CappoUncertainError("target_timeout") from exc

    def read_state(self, context: ConsequenceContext) -> object:
        raise KeyError(context.resource)
