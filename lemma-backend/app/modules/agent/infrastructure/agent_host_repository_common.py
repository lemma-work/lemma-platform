"""Shared errors and defaults for Agent Host repositories."""

from datetime import datetime, timezone


DEFAULT_PAIRING_TTL_SECONDS = 600
DEFAULT_COMMAND_TTL_SECONDS = 300
DEFAULT_RUN_LEASE_SECONDS = 90


class AgentHostRepositoryError(RuntimeError):
    """Base typed failure for the Agent Host persistence contract."""


class AgentHostNotFound(AgentHostRepositoryError):
    pass


class AgentHostPairingRejected(AgentHostRepositoryError):
    pass


class AgentHostProtocolViolation(AgentHostRepositoryError):
    pass


class AgentHostRunConflict(AgentHostRepositoryError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
