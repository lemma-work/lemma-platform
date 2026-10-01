"""The MCP frame in a queued host command is sealed to its run.

It carries a run-scoped Lemma credential, so the command row holds only the
sealed form, and a frame copied onto another run's command does not open.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
)
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.mcp_frame import (
    open_mcp_frame,
    seal_mcp_frame,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostProtocolViolation,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostCommandModel
from app.modules.test_support.mappers import configure_test_mappers
from app.modules.test_support.vault_fake import StaticSealingKeys

# Building a model row configures the mapper graph. See `configure_test_mappers`.
configure_test_mappers()

_FRAME = {
    "url": "https://lemma.test/mcp",
    "headers": {"Authorization": "Bearer CANARY"},
}


class _UnitOfWork:
    session = None


def _command(run_id, payload) -> AgentHostCommandModel:
    now = datetime.now(timezone.utc)
    return AgentHostCommandModel(
        id=uuid7(),
        host_id=uuid4(),
        run_id=run_id,
        kind=AgentHostCommandKind.REFRESH_CREDENTIAL.value,
        lease_epoch=1,
        payload=payload,
        state=AgentHostCommandState.QUEUED.value,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


async def test_the_frame_rests_sealed_and_opens_for_its_run() -> None:
    keys = StaticSealingKeys()
    run_id = uuid4()

    sealed = await seal_mcp_frame(_FRAME, run_id=run_id, keyring=keys)

    assert sealed.startswith("lvs1:")
    assert "CANARY" not in sealed
    assert await open_mcp_frame(sealed, run_id=run_id, keyring=keys) == _FRAME


async def test_a_frame_sealed_for_one_run_does_not_open_for_another() -> None:
    keys = StaticSealingKeys()
    sealed = await seal_mcp_frame(_FRAME, run_id=uuid4(), keyring=keys)

    with pytest.raises(AgentHostProtocolViolation):
        await open_mcp_frame(sealed, run_id=uuid4(), keyring=keys)


@pytest.mark.parametrize("stored", [{"_encrypted": "lemma-secret-v2"}, "plaintext"])
async def test_anything_but_a_sealed_frame_is_a_protocol_violation(stored) -> None:
    with pytest.raises(AgentHostProtocolViolation):
        await open_mcp_frame(stored, run_id=uuid4(), keyring=StaticSealingKeys())


async def test_delivery_puts_the_opened_frame_on_the_wire() -> None:
    keys = StaticSealingKeys()
    repository = AgentHostDispatchRepository(
        _UnitOfWork(),  # type: ignore[arg-type]
        keyring=keys,
    )
    run_id = uuid4()
    command = _command(
        run_id,
        {"encrypted_mcp": await seal_mcp_frame(_FRAME, run_id=run_id, keyring=keys)},
    )

    wire = await repository._wire_command(command)

    assert wire.payload == {"mcp": _FRAME}
    # The row keeps only the sealed form.
    assert set(command.payload) == {"encrypted_mcp"}


async def test_delivery_refuses_a_frame_moved_onto_another_runs_command() -> None:
    keys = StaticSealingKeys()
    repository = AgentHostDispatchRepository(
        _UnitOfWork(),  # type: ignore[arg-type]
        keyring=keys,
    )
    command = _command(
        uuid4(),
        {"encrypted_mcp": await seal_mcp_frame(_FRAME, run_id=uuid4(), keyring=keys)},
    )

    with pytest.raises(AgentHostProtocolViolation):
        await repository._wire_command(command)
