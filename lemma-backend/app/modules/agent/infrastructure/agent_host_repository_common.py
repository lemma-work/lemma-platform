"""Shared errors and defaults for Agent Host repositories."""

from datetime import datetime, timezone


DEFAULT_PAIRING_TTL_SECONDS = 600


class AgentHostRepositoryError(RuntimeError):
    """Base typed failure for the Agent Host persistence contract."""

    code = "AGENT_HOST_ERROR"


class AgentHostNotFound(AgentHostRepositoryError):
    code = "AGENT_HOST_NOT_FOUND"


class AgentHostPairingRejected(AgentHostRepositoryError):
    code = "AGENT_HOST_PAIRING_REJECTED"


class AgentHostProtocolViolation(AgentHostRepositoryError):
    code = "AGENT_HOST_PROTOCOL_VIOLATION"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
