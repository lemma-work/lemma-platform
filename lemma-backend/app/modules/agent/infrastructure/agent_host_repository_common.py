"""Shared errors and defaults for Agent Host repositories."""

from datetime import datetime, timezone


DEFAULT_PAIRING_TTL_SECONDS = 600
DEFAULT_COMMAND_TTL_SECONDS = 300
DEFAULT_RUN_LEASE_SECONDS = 90

# A permission decision has to outlive the window the agent will wait in, not
# the window a host is normally offline for. The host holds an ACP permission
# request open for 30 minutes; a decision the user makes at minute six is
# useless if the command carrying it expired at minute five while their laptop
# was asleep, because the agent then times the request out as unanswered.
DEFAULT_PERMISSION_COMMAND_TTL_SECONDS = 1800


class AgentHostRepositoryError(RuntimeError):
    """Base typed failure for the Agent Host persistence contract."""

    code = "AGENT_HOST_ERROR"


class AgentHostNotFound(AgentHostRepositoryError):
    code = "AGENT_HOST_NOT_FOUND"


class AgentHostPairingRejected(AgentHostRepositoryError):
    code = "AGENT_HOST_PAIRING_REJECTED"


class AgentHostProtocolViolation(AgentHostRepositoryError):
    code = "AGENT_HOST_PROTOCOL_VIOLATION"


class AgentHostRunConflict(AgentHostRepositoryError):
    code = "AGENT_HOST_RUN_CONFLICT"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
