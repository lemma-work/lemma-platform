"""A run's session for a relayed (Agent Host MCP) tool call, and what it keeps.

Every Lemma tool call an Agent Host relays loads the run's session
(``load_relayed_session``): the conversation, run and agent, the recorded
workspace, and the runtime and grants -- the last two kept for the run's lease.

What every Agent Host tool call re-derived, kept for the run's lease.

A coding agent makes hundreds of Lemma tool calls a turn, and each one resolved
the run's runtime profile and read the agent's grants again to build the same
toolsets. Neither changes inside a lease often enough to be worth that: the
runtime is fixed when the run starts, and grants change when a person edits
them. So both are kept in Redis under the run and its lease epoch, for a short
while, and thrown away early when the agent's grants change
(``core/authorization/grant_generation``).

The runtime is kept without its provider ``config``: the bridge builds no model
client -- it holds no credentials to build one with -- and reads only what the
runtime can do, while the config may carry a provider's custom headers, which
have no business sitting in a cache.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.grant_generation import agent_grant_generation
from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.domain.workspace_location import ensure_recorded_location
from app.modules.agent.agent_host.control.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.domain.toolsets import AgentGrantSummary
from app.modules.agent.infrastructure.tools.callables import load_agent_grant_summary

if TYPE_CHECKING:
    from app.modules.agent.services.run_session import RunSession

logger = get_logger(__name__)

#: Long enough to cover a burst of calls, short enough that a profile edited
#: mid-run is seen within a minute.
MCP_INPUTS_TTL_SECONDS = 60
#: The grant token of a run with no agent of its own: the pod's assistant,
#: which holds no grants, so there is nothing to change.
NO_AGENT_GENERATION = "no-agent"


class CachedGrantSummary(BaseModel):
    function_ids: list[str]
    agent_ids: list[str]
    has_pod_data: bool
    has_connectors: bool

    @classmethod
    def of(cls, summary: AgentGrantSummary) -> CachedGrantSummary:
        return cls(
            function_ids=list(summary.function_ids),
            agent_ids=list(summary.agent_ids),
            has_pod_data=summary.has_pod_data,
            has_connectors=summary.has_connectors,
        )

    def summary(self) -> AgentGrantSummary:
        return AgentGrantSummary(
            function_ids=tuple(self.function_ids),
            agent_ids=tuple(self.agent_ids),
            has_pod_data=self.has_pod_data,
            has_connectors=self.has_connectors,
        )


class RunInputs(BaseModel):
    """The derived inputs, and the grant token they were derived under."""

    grant_generation: str
    runtime_profile: dict[str, object] | None
    grants: CachedGrantSummary


def without_provider_config(
    runtime_profile: dict[str, object] | None,
) -> dict[str, object] | None:
    if runtime_profile is None:
        return None
    return {key: value for key, value in runtime_profile.items() if key != "config"}


class JsonStore(Protocol):
    """The part of ``RedisJsonCache`` this uses."""

    async def get_json(self, suffix: str) -> object | None: ...

    async def set_json(self, suffix: str, value: object) -> None: ...


class RunInputsCache:
    def __init__(self, cache: JsonStore) -> None:
        self._cache = cache

    async def get(
        self, *, run_id: UUID, lease_epoch: int, grant_generation: str
    ) -> RunInputs | None:
        """The inputs kept for this lease, unless the agent's grants moved on."""
        try:
            raw = await self._cache.get_json(_suffix(run_id, lease_epoch))
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "agent.mcp_tool_inputs.read_failed.degraded",
                agent_run_id=str(run_id),
                exc_info=True,
            )
            return None
        if raw is None:
            return None
        try:
            inputs = RunInputs.model_validate(raw)
        except ValidationError:
            # Written by a release with another shape: derive afresh.
            return None
        return inputs if inputs.grant_generation == grant_generation else None

    async def put(self, *, run_id: UUID, lease_epoch: int, inputs: RunInputs) -> None:
        try:
            await self._cache.set_json(
                _suffix(run_id, lease_epoch), inputs.model_dump(mode="json")
            )
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "agent.mcp_tool_inputs.write_failed.degraded",
                agent_run_id=str(run_id),
                exc_info=True,
            )


def _suffix(run_id: UUID, lease_epoch: int) -> str:
    return f"{run_id}:{lease_epoch}"


@lru_cache(maxsize=1)
def run_inputs_cache() -> RunInputsCache:
    return RunInputsCache(
        RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="agent:mcp-inputs",
            ttl_seconds=MCP_INPUTS_TTL_SECONDS,
        )
    )


class AgentRemoved(ValueError):
    """The run's agent was deleted; its tools must not run as anybody else."""


async def load_relayed_session(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation_id: UUID,
    agent_run_id: UUID | None,
    inputs_cache: RunInputsCache,
) -> RunSession:
    """The run's session for one relayed tool call, its context's inputs kept.

    Reads the conversation, the run (the one named, else the active one), the
    agent and the lease in one unit of work, recording the cwd if nothing had;
    then, outside it, the runtime and grants kept for the lease.
    """
    # Imported here: the run session's graph (history, briefs) has no place
    # in the startup imports of the API process that serves the host link.
    from app.modules.agent.services.run_session import RunSession
    from app.modules.agent.services.surface_context import conversation_surface

    async with uow_factory() as uow:
        conversations = Conversations(uow)
        runs = Runs(uow)
        conversation = await conversations.get_conversation(
            conversation_id, include_runs=True
        )
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        run = None
        if agent_run_id is not None:
            run = await runs.get_agent_run(agent_run_id)
            if run is not None and str(run.conversation_id) != str(conversation_id):
                # `authorize` refuses this first; kept so no caller can build a
                # context from another conversation's run.
                raise ValueError(
                    f"Run {agent_run_id} is not in conversation {conversation_id}"
                )
        if run is None:
            run = await runs.get_active_agent_run(conversation_id)
        agent_id = conversation.agent_id or (run.agent_id if run else None)
        agent = await AgentRepository(uow).get(agent_id) if agent_id else None
        if agent_id is not None and agent is None:
            # Gone between the call's authorization and now. Falling through
            # would read the run as the pod's own assistant, which acts with
            # the person's full standing.
            raise AgentRemoved(f"Agent {agent_id} no longer exists")
        # Records the cwd when nothing had recorded it, so the row is the
        # source of truth in fact and not only by intention.
        workspace = await ensure_recorded_location(
            conversation, record=conversations.record_cwd
        )
        lease = (
            await uow.session.get(AgentHostRunLeaseModel, run.id)
            if run is not None
            else None
        )
        surface = await conversation_surface(uow, conversation.id)
    # Outside the unit of work: these open their own, or reach Redis, and
    # holding this one across them would pin a pooled connection for nothing.
    inputs = await _kept_inputs(
        uow_factory,
        inputs_cache,
        run=run,
        lease_epoch=lease.lease_epoch if lease is not None else 0,
        agent=agent,
        conversation=conversation,
    )
    return RunSession(
        conversation=conversation,
        agent=agent,
        run=run,
        workspace=workspace,
        runtime_profile=inputs.runtime_profile if inputs else None,
        grants=inputs.grants.summary() if inputs else None,
        surface=surface,
    )


async def _kept_inputs(
    uow_factory: UnitOfWorkFactory,
    cache: RunInputsCache,
    *,
    run: AgentRun | None,
    lease_epoch: int,
    agent: Agent | None,
    conversation: Conversation,
) -> RunInputs | None:
    """The run's resolved runtime and its agent's grants, kept for the lease.

    ``None`` with no run: nothing to resolve, and the assembler reads the
    grants itself. A grant token that could not be read is neither trusted nor
    stored: the inputs are derived afresh.
    """
    if run is None:
        return None
    generation = (
        await agent_grant_generation(agent.id)
        if agent is not None and agent.id is not None
        else NO_AGENT_GENERATION
    )
    if generation is not None:
        kept = await cache.get(
            run_id=run.id, lease_epoch=lease_epoch, grant_generation=generation
        )
        if kept is not None:
            return kept
    async with uow_factory() as uow:
        runtime_profile = await resolved_runtime_profile(
            run=run,
            uow=uow,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
        )
    grants = (
        await load_agent_grant_summary(uow_factory, agent=agent)
        if agent is not None
        else AgentGrantSummary()
    )
    derived = RunInputs(
        grant_generation=generation or "",
        runtime_profile=without_provider_config(runtime_profile),
        grants=CachedGrantSummary.of(grants),
    )
    if generation is not None:
        await cache.put(run_id=run.id, lease_epoch=lease_epoch, inputs=derived)
    return derived


async def resolved_runtime_profile(
    *,
    run: AgentRun | None,
    uow: SqlAlchemyUnitOfWork,
    organization_id: UUID | None,
    user_id: UUID,
) -> JsonObject | None:
    """The run's runtime, resolved, so its capabilities are actually present.

    `run.agent_runtime` is a profile id and a model name and nothing else; it
    has no `model_capabilities`, so deriving the vision mode from it answered
    "this model cannot see" for every remote harness. Resolving is what
    produces capabilities, and costs one indexed lookup on this unit of work
    and no network call.

    Falls back to the unresolved config on failure: a tool call must not fail
    because a profile was archived between the run starting and the model
    reaching for a file.
    """
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
    )

    if run is None:
        return None
    stored = run.agent_runtime.model_dump(mode="json")
    try:
        service = AgentRuntimeProfileService(
            AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
            # Passed so a harness that has since reported it reads images is
            # believed, rather than the catalog copied from it before its probe
            # landed.
            AgentHostRepository(uow),
        )
        resolved = await service.resolve(
            runtime=run.agent_runtime,
            organization_id=organization_id,
            user_id=user_id,
        )
    except (DomainError, RuntimeError, SQLAlchemyError) as exc:
        raise_if_database_unavailable(exc)
        # Named rather than bare: these are what resolution actually fails
        # with. Anything else is a bug and should surface as one.
        logger.warning(
            "agent.conversation_mcp.runtime_resolve_failed.degraded",
            exc_info=True,
        )
        return stored
    return resolved.runtime_snapshot().as_payload()


async def recorded_native_commands_for(
    conversation: Conversation, run: AgentRun | None
) -> bool:
    """What the run decided when its context was first built, read back.

    Recomputing it here let a host-execution toggle mid-run change the toolset
    under a prompt that said otherwise. Only a run with no record -- one
    dispatched before runs kept it -- is asked afresh. Imported here to keep
    host selection out of the startup import graph.
    """
    from app.modules.agent.agent_host.control.native_commands import (
        recorded_native_commands,
    )

    if run is None:
        # No run, no way to tell who started it: keep Lemma's tools, which
        # never wait on a person.
        return False
    recorded = await recorded_native_commands(run.id)
    if recorded is not None:
        return recorded
    from app.modules.agent.agent_host.control.host_execution_selection import (
        host_runs_native_commands,
    )

    return await host_runs_native_commands(conversation, run)
