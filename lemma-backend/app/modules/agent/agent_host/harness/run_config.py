"""Reading a dispatched run's settings out of its runtime profile.

Separated from the harness because these are pure translation: what the
profile says becomes what the run spec carries. The harness is about
driving a run once those are settled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.selections import (
    carry_agent_host_selections,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import JsonObject

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AgentHostRunConfig:
    harness_id: UUID
    runtime_profile_id: UUID
    config_selections: JsonObject
    wait_timeout_seconds: int
    model_name: str | None


def agent_host_run_config[DepsT](options: HarnessOptions) -> AgentHostRunConfig:
    snapshot = options.runtime_profile
    if snapshot is None:
        raise ValueError("runtime profile is missing from harness options")
    # Validated here so a profile with no saved revision fails before dispatch,
    # naming the field, rather than as a KeyError. Admission intentionally
    # uses the latest live revision after selections are revalidated by the
    # repository.
    config = snapshot.harness_config()
    # The model comes from the profile snapshot rather than options.model_name,
    # which substitutes a "default" placeholder when nothing is pinned. Agent
    # Host rejects any model the harness does not advertise, so an unpinned
    # profile must send no model and let the harness use its own default.
    raw_model = snapshot.provider_model_name or snapshot.model_name
    model_name = raw_model.strip() if raw_model else None
    return AgentHostRunConfig(
        harness_id=snapshot.harness_uuid(),
        runtime_profile_id=UUID(snapshot.profile_id),
        # The profile's, then the run's own (pod default, teammate,
        # conversation -- layered when the turn started), narrowest per key.
        # Not yet checked against the harness: `offered_selections` drops what
        # it no longer offers once dispatch has read it.
        config_selections={
            **config.config_selections,
            **snapshot.selection_overrides,
        },
        wait_timeout_seconds=config.host_wait_timeout_seconds,
        model_name=model_name or None,
    )


def offered_selections(
    run_config: AgentHostRunConfig,
    *,
    config_options: list[object],
    agent_run_id: UUID,
) -> AgentHostRunConfig:
    """``run_config`` keeping only the selections the harness offers right now.

    For the run's model: an effort one model offers and another does not is
    dropped when the run is on the other. Dropped, not refused -- a selection
    saved against an earlier publish is news about the harness, not a mistake
    by the person sending this message, and the host reports one it cannot
    apply as ``selection_unavailable`` anyway.
    """
    carried = carry_agent_host_selections(
        config_options=config_options,
        selections=run_config.config_selections,
        model_name=run_config.model_name,
    )
    dropped = sorted(set(run_config.config_selections) - set(carried))
    if not dropped:
        return run_config
    logger.info(
        "agent.harnesses.agent_host.selections_dropped",
        agent_run_id=str(agent_run_id),
        runtime_profile_id=str(run_config.runtime_profile_id),
        dropped_selections=dropped,
    )
    return replace(run_config, config_selections=carried)


def resolve_pod_cwd(conversation: Conversation) -> str:
    from app.modules.agent.domain.workspace_location import resolve_pod_cwd

    return resolve_pod_cwd(conversation)


def json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}
