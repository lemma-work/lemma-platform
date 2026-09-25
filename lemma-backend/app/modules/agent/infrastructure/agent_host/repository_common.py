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


class AgentHostStaleLease(AgentHostProtocolViolation):
    """A batch for a lease epoch that has since been superseded.

    The three protocol violations below are subclasses rather than new codes so
    everything that already catches ``AgentHostProtocolViolation`` keeps doing
    so. They exist because the link answers each with its own error code: the
    host drops its outbox for a run it no longer owns, resends from the
    watermark after a gap, and stops sending to a run that already ended.
    Distinguishing them used to mean reading the message text of one 409.
    """

    code = "AGENT_HOST_STALE_LEASE"


class AgentHostSequenceGap(AgentHostProtocolViolation):
    code = "AGENT_HOST_SEQUENCE_GAP"


class AgentHostTerminalRun(AgentHostProtocolViolation):
    code = "AGENT_HOST_TERMINAL_RUN"


class AgentHostRunConflict(AgentHostRepositoryError):
    code = "AGENT_HOST_RUN_CONFLICT"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
