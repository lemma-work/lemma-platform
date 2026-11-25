"""The Lemma MCP frame a queued host command carries, sealed to its run.

The frame holds a run-scoped credential, so it rests sealed inside the command
row and is opened only when the command is handed to the host. The run id is
bound into the seal: a frame copied onto another run's command does not open.

A command queued by the previous release during the rollout carries the frame
Fernet-encrypted instead (a JSON object, not an ``lvs1:`` string); it is read
with ``legacy_json`` until the next release drops that path.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from app.modules.agent.agent_host.control.repository_common import (
    AgentHostProtocolViolation,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.vault.contracts import (
    SealedValueInvalid,
    SealingKeys,
    SecretValue,
    is_sealed,
    legacy_json,
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
    if isinstance(sealed, dict):
        # Queued by the previous release (Fernet).
        legacy = await legacy_json(sealed)
        if legacy is None:
            raise AgentHostProtocolViolation("MCP payload is unavailable")
        return dict(legacy)
    if run_id is None or not is_sealed(sealed):
        raise AgentHostProtocolViolation("MCP payload is unavailable")
    try:
        return dict(
            await open_json(
                cast(str, sealed),
                purpose=MCP_FRAME_PURPOSE,
                bindings=[str(run_id)],
                keyring=keyring,
            )
        )
    except SealedValueInvalid as exc:
        raise AgentHostProtocolViolation("MCP payload is unavailable") from exc
