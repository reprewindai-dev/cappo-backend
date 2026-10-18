"""Errors raised by the capability mount core."""


class MountError(Exception):
    """Raised when a capability package cannot be mounted."""


class PolicyError(Exception):
    """Raised when an execution action is denied by policy."""


class TokenExpiredError(PolicyError):
    """Raised when an execution token is no longer live."""


class ExecutionTerminatedError(PolicyError):
    """Raised when an execution has been explicitly terminated."""


class TargetRefusedError(PolicyError):
    """Raised by a target adapter when the target declined to commit the consequence.

    Typically the target redeemed the permit and CAPPO answered DENY (authority ended,
    payload changed, already redeemed). Nothing was committed: the consequence is recorded
    FAILED and the caller receives a DENY, not a server error.
    """
