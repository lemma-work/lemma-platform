"""The Agent Host link, as the workspace module serves its ``compute`` role.

See docs/architecture/connected-machines.md. A connected machine speaks the
link the agent module owns; the workspace module serves what travels on it --
pairing, the machine's report, the mux. The envelope, the writer, the close
codes, ownership by link generation and the notice channel stay the agent
module's, and are published here rather than reached into, so the link's
internals stay its own to change.
"""

from __future__ import annotations

from app.modules.agent.domain.agent_host import AGENT_HOST_PROTOCOL_VERSION
from app.modules.agent.domain.agent_host_link import (
    AGENT_HOST_LINK_HEARTBEAT_MS,
    AGENT_HOST_LINK_MISSED_HEARTBEATS,
    REVOKED_OR_MISSING_REASON,
    HostFrameType,
    LinkCloseCode,
    LinkErrorCode,
    LinkFrame,
    ServerFrameType,
)
from app.modules.agent.domain.agent_host_link_compute import (
    COMPUTE_MIN_PROTOCOL_VERSION,
    COMPUTE_PAIRING_TTL_SECONDS,
    ComputeControlBody,
    ComputeControlOkBody,
    ComputeHelloBody,
    ComputePairBody,
    ComputePairedBody,
    ComputeWelcomeBody,
    MachineMode,
    MachineReport,
    RuntimeBundleTarget,
    RuntimeGrant,
    StreamTargetKind,
)
from app.modules.agent.infrastructure.agent_host.channels import (
    REVOKED,
    parse_host_notice,
)
from app.modules.agent.services.agent_host_link_mcp import notice_stream
from app.modules.agent.services.agent_host_link_ownership import LinkOwnership
from app.modules.agent.services.agent_host_link_wire import (
    TRANSIENT_ERRORS,
    EmptyBody,
    LinkClose,
    LinkSocket,
    LinkWriter,
    bearer_secret,
    log_host_reported_error,
    validation_summary,
)

__all__ = [
    "AGENT_HOST_LINK_HEARTBEAT_MS",
    "AGENT_HOST_LINK_MISSED_HEARTBEATS",
    "AGENT_HOST_PROTOCOL_VERSION",
    "COMPUTE_MIN_PROTOCOL_VERSION",
    "COMPUTE_PAIRING_TTL_SECONDS",
    "REVOKED",
    "REVOKED_OR_MISSING_REASON",
    "TRANSIENT_ERRORS",
    "ComputeControlBody",
    "ComputeControlOkBody",
    "ComputeHelloBody",
    "ComputePairBody",
    "ComputePairedBody",
    "ComputeWelcomeBody",
    "EmptyBody",
    "HostFrameType",
    "LinkClose",
    "LinkCloseCode",
    "LinkErrorCode",
    "LinkFrame",
    "LinkOwnership",
    "LinkSocket",
    "LinkWriter",
    "MachineMode",
    "MachineReport",
    "RuntimeBundleTarget",
    "RuntimeGrant",
    "ServerFrameType",
    "StreamTargetKind",
    "bearer_secret",
    "log_host_reported_error",
    "notice_stream",
    "parse_host_notice",
    "validation_summary",
]
