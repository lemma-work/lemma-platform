"""Whether a teammate's model can run, asked before anything depends on it.

A retired or unknown model used to surface only in the worker, after the run
row existed: a pinned model missing from the catalog was quietly swapped for
another, and one still listed but dropped by the provider failed with a 404 on
the first completion -- for every concurrent run independently, and again on
every retry, which re-used the dead runtime.

So the same check now runs in three places, cheaply (one indexed read, one
Redis read, no provider call): when a runtime is saved on an agent or a
conversation, when a turn starts, and when a failed run is retried. A provider's
404 for a model is remembered for a while, so the next turn is refused at once
instead of paying for the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.unavailable_models import (
    UnavailableModels,
    unavailable_models,
)
from app.modules.agent.services.runtime_resolution import (
    organization_or_system_runtime,
    pod_default_runtime,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    runtime_profile_service_for,
    runtime_profile_unavailable_error,
)
from app.modules.agent.services.runtime_profile_service import selected_model
from app.modules.agent.domain.resolved_runtime import RuntimeHolder
from app.modules.agent.services.runtime_selection_check import (
    check_runtime_config_selections,
)
from app.modules.agent.services.runtime_system_profiles import (
    SYSTEM_LEMMA_PROFILE_ID,
    is_model_not_configured,
)
from app.modules.pod.contracts.agent_access import pod_organization_id

logger = get_logger(__name__)


async def check_runtime_selection(
    runtime: AgentRuntimeConfig,
    *,
    profiles: AgentRuntimeProfileService,
    organization_id: UUID | None,
    user_id: UUID,
    saving_for: RuntimeHolder | None = None,
) -> SelectedModel | None:
    """Refuse a runtime ``user_id`` cannot run, with a :class:`RuntimeUnavailableError`.

    Visibility is the whole rule for whose runtime it is. A member's own
    personal profile -- their own Claude Code, their own key -- passes for them,
    on a teammate the pod sees too: pinning one there is how a person puts their
    own coding agent to work in a pod. Anybody else is refused, at save and at
    their turn alike, with the owner named. Returns the model it settled on,
    when one is pinned, for the provider-refusal check a turn adds; reads only
    the database, so a save can run it on a request's session.
    """
    try:
        profile = await profiles.get_profile(
            profile_id=runtime.profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
    except DomainError as error:
        if is_model_not_configured(error):
            # The deployment's own model is not set up. The run says so in its
            # own words; refusing the save would block every teammate on it.
            return None
        raise
    if profile is None:
        if runtime.profile_id == SYSTEM_LEMMA_PROFILE_ID:
            return None
        # Resolving says why, in the words a run would have used: archived
        # (and where to bring it back), or gone, or someone else's.
        await profiles.resolve(
            runtime=runtime, organization_id=organization_id, user_id=user_id
        )
        raise runtime_profile_unavailable_error()
    model = selected_model(profile, runtime.model_name, saving_for=saving_for)
    if model is None:
        return None
    return SelectedModel(
        profile_id=profile.id, profile_name=profile.name, model_name=model.name
    )


@dataclass(frozen=True, slots=True)
class SelectedModel:
    profile_id: str
    profile_name: str
    model_name: str


async def refuse_a_model_the_provider_refused(
    selected: SelectedModel | None, unavailable: UnavailableModels
) -> None:
    """Fail fast on a model a provider just answered 404 for."""
    if selected is None or not await unavailable.is_marked(
        profile_id=selected.profile_id, model_name=selected.model_name
    ):
        return
    raise RuntimeUnavailableError(
        f"{selected.profile_name} just answered that it does not serve "
        f"{selected.model_name}. Pick another model for this teammate, or "
        "check the provider in Settings → Models."
    )


def profile_service(uow: SqlAlchemyUnitOfWork) -> AgentRuntimeProfileService:
    return runtime_profile_service_for(uow)


class SaveRuntimeCheck(Protocol):
    """The save-time check, as a seam for a service built from fakes."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        runtime: AgentRuntimeConfig | None,
        *,
        pod_id: UUID,
        user_id: UUID,
    ) -> None: ...


async def check_runtime_for_pod(
    uow: SqlAlchemyUnitOfWork,
    runtime: AgentRuntimeConfig | None,
    *,
    pod_id: UUID,
    user_id: UUID,
    saving_for: RuntimeHolder = RuntimeHolder.TEAMMATE,
) -> None:
    """The save-time check: the runtime an agent or conversation is given.

    Its coding-agent selections too, which only a save checks strictly; a turn
    drops one the harness has stopped offering instead.
    """
    if runtime is None:
        return
    profiles = profile_service(uow)
    organization_id = await pod_organization_id(uow, pod_id)
    await check_runtime_selection(
        runtime,
        profiles=profiles,
        organization_id=organization_id,
        user_id=user_id,
        saving_for=saving_for,
    )
    await check_runtime_config_selections(
        runtime,
        profiles=profiles,
        organization_id=organization_id,
        user_id=user_id,
    )


async def check_conversation_runtime_for_pod(
    uow: SqlAlchemyUnitOfWork,
    runtime: AgentRuntimeConfig | None,
    *,
    pod_id: UUID,
    user_id: UUID,
) -> None:
    """The save-time check, for the runtime a conversation is given."""
    await check_runtime_for_pod(
        uow,
        runtime,
        pod_id=pod_id,
        user_id=user_id,
        saving_for=RuntimeHolder.CONVERSATION,
    )


async def turn_runtime(
    uow: SqlAlchemyUnitOfWork, conversation: Conversation, agent: Agent
) -> AgentRuntimeConfig:
    """The runtime a new turn on this conversation runs on, as it stands now.

    A conversation that picked its own model keeps the agent's generation
    settings unless it chose its own: switching the model for one chat is not
    a decision to forget how the agent was told to think.

    The runtime is the narrowest one set, but a coding agent's selections
    layer: a conversation that picks only an effort keeps the teammate's mode
    and the pod's defaults (``AgentRuntimeConfig.layered_over``). So the pod's
    own default is read even when something narrower names the runtime.
    """
    pod_runtime = await pod_default_runtime(uow, pod_id=conversation.pod_id)
    selected = (
        conversation.agent_runtime
        or agent.agent_runtime
        or pod_runtime
        or await organization_or_system_runtime(uow, pod_id=conversation.pod_id)
    )
    return selected.inheriting_settings_from(agent.agent_runtime).layered_over(
        [pod_runtime, agent.agent_runtime]
    )


class TurnRuntimeCheck(Protocol):
    """The turn-start check, as a seam for a coordinator built from fakes."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        runtime: AgentRuntimeConfig,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> None: ...


async def ensure_turn_runtime_available(
    uow: SqlAlchemyUnitOfWork,
    runtime: AgentRuntimeConfig,
    *,
    organization_id: UUID | None,
    user_id: UUID,
) -> None:
    """The turn-start check, before a run row exists to fail."""
    selected = await check_runtime_selection(
        runtime,
        profiles=profile_service(uow),
        organization_id=organization_id,
        user_id=user_id,
    )
    await refuse_a_model_the_provider_refused(selected, unavailable_models())
