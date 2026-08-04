"""Errors raised by the capability mount core."""


class MountError(Exception):
    """Raised when a capability package cannot be mounted."""


class PolicyError(Exception):
    """Raised when an execution action is denied by policy."""


class TokenExpiredError(PolicyError):
    """Raised when an execution token is no longer live."""


class ExecutionTerminatedError(PolicyError):
    """Raised when an execution has been explicitly terminated."""
