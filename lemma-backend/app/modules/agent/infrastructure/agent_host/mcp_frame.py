"""The Lemma MCP frame a queued host command carries, sealed to its run.

The frame holds a run-scoped credential, so it rests sealed inside the command
row and is opened only when the command is handed to the host. The run id is
bound into the seal: a frame copied onto another run's command does not open.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostProtocolViolation,
)
from app.modules.vault.contracts import (
    SealedValueInvalid,
    SealingKeys,
    SecretValue,
    open_json,
    seal_value,
)

MCP_FRAME_PURPOSE = "agent.agent_host.mcp"


async def seal_mcp_frame(
    mcp: JsonObject, *, run_id: UUID, keyring: SealingKeys | None = None
) -> str:
    return await seal_value(
        cast(SecretValue, mcp),
        purpose=MCP_FRAME_PURPOSE,
        bindings=[str(run_id)],
        keyring=keyring,
    )


async def open_mcp_frame(
    sealed: object, *, run_id: UUID | None, keyring: SealingKeys | None = None
) -> JsonObject:
    """The frame, or :class:`AgentHostProtocolViolation` if it cannot be opened."""
    if run_id is None or not isinstance(sealed, str):
        raise AgentHostProtocolViolation("MCP payload is unavailable")
    try:
        return dict(
            await open_json(
                sealed,
                purpose=MCP_FRAME_PURPOSE,
                bindings=[str(run_id)],
                keyring=keyring,
            )
        )
    except SealedValueInvalid as exc:
        raise AgentHostProtocolViolation("MCP payload is unavailable") from exc
