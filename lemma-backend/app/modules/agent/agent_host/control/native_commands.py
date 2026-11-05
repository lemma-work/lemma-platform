"""Whether an Agent Host run's own shell replaces Lemma's command tools. Decided once.

docs/architecture/desktop-host-execution.md §7: a coding agent on a Mac with
host execution on already has a shell and file tools there, so Lemma withholds
its duplicates -- but only for a run its user started in the app
(``host_runs_native_commands``): the agent's own shell asks a person before
each command, so a workflow or schedule run keeps Lemma's tools, which do not.
The answer shapes three things that have to agree for the whole
run -- the prompt dispatch writes, the tool names the host is told it may
auto-allow, and the tools every relayed MCP call is served from.

It used to be asked again on every MCP call. Turning host execution off or on
mid-run then changed the toolset under a prompt that said otherwise: the
command tools vanished as "unknown tool", or appeared outside the host's
auto-allow list and raised approval cards. And the question asked was whether
the person had *any* usable host, not whether the run's own host was one.

So it is decided when the run's context is first built, for the host the run's
harness is on, and written on the run's record (``run_records``); every later
context build for the run -- a reclaimed worker, every relayed tool call --
reads it back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.agent_host.control.host_execution import (
    host_execution_host,
)
from app.modules.agent.agent_host.control.repository import (
    AgentHostRepository,
)
from app.modules.agent.agent_host.control.run_records import (
    read_run_record,
    record_run_field,
)
from app.modules.agent.agent_host.control.host_execution_selection import (
    host_runs_native_commands,
)


async def _harness_host(harness_id: UUID) -> UUID | None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        harness = await AgentHostRepository(uow).get_harness(harness_id=harness_id)
    return harness.host_id if harness is not None else None


async def _usable_for(user_id: UUID, host_id: UUID) -> bool:
    """Whether ``host_id`` is one of ``user_id``'s hosts with host execution usable."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        host = await host_execution_host(uow, user_id=user_id, prefer=host_id)
    return host is not None and host.id == host_id


async def _recorded(run_id: UUID) -> dict[str, object] | None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await read_run_record(uow, run_id, "host_native_commands")


async def _record(run_id: UUID, value: dict[str, object]) -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await record_run_field(uow, run_id, "host_native_commands", value)
        await uow.commit()


@dataclass(frozen=True, slots=True)
class NativeCommandFacts:
    """Where each answer comes from; injected, so a test can state them."""

    #: Whether host execution applies to this run at all: its user started it,
    #: and has a usable host.
    applies: Callable[[Conversation, AgentRun], Awaitable[bool]] = (
        host_runs_native_commands
    )
    harness_host: Callable[[UUID], Awaitable[UUID | None]] = _harness_host
    usable_for: Callable[[UUID, UUID], Awaitable[bool]] = _usable_for
    recorded: Callable[[UUID], Awaitable[dict[str, object] | None]] = _recorded
    record: Callable[[UUID, dict[str, object]], Awaitable[None]] = _record


FACTS = NativeCommandFacts()


async def settle_native_commands(
    *,
    conversation: Conversation,
    agent_run: AgentRun,
    harness_id: UUID | None,
    facts: NativeCommandFacts = FACTS,
) -> bool:
    """This run's answer: read back if it has one, otherwise decided and recorded."""
    recorded = await facts.recorded(agent_run.id)
    if recorded is not None:
        return recorded.get("enabled") is True
    host_id = await facts.harness_host(harness_id) if harness_id is not None else None
    enabled = (
        host_id is not None
        and await facts.applies(conversation, agent_run)
        and await facts.usable_for(conversation.user_id, host_id)
    )
    await facts.record(
        agent_run.id,
        {"enabled": enabled, "host_id": str(host_id) if host_id else None},
    )
    return enabled


async def recorded_native_commands(
    run_id: UUID, *, facts: NativeCommandFacts = FACTS
) -> bool | None:
    """What this run's context build decided, or ``None`` if it decided nothing."""
    recorded = await facts.recorded(run_id)
    return None if recorded is None else recorded.get("enabled") is True


__all__ = [
    "FACTS",
    "NativeCommandFacts",
    "recorded_native_commands",
    "settle_native_commands",
]
