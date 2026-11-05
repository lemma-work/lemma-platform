"""Whether a runtime's own coding-agent selections are ones its harness offers.

A pod default, a teammate and a conversation may each carry selections -- an
effort, a mode -- for an Agent Host runtime (``AgentRuntimeConfig.
config_selections``). Each is checked when it is saved, against the options the
harness last published for that runtime's model, so a typo or a value the
machine never offered is refused with the reason instead of being dropped
quietly at the first run. The run itself is lenient: a value the harness has
since stopped offering is dropped at dispatch (``offered_selections``).

Its own module because ``runtime_availability`` is about whether a runtime can
run at all, and this is a different question asked only on save.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.agent.domain.agent_host_selections import (
    validate_agent_host_selections,
)
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)


async def check_runtime_config_selections(
    runtime: AgentRuntimeConfig,
    *,
    profiles: AgentRuntimeProfileService,
    organization_id: UUID | None,
    user_id: UUID,
) -> None:
    """Refuse selections the runtime's harness does not offer for its model.

    Raises :class:`RuntimeProfileValidationError`, the error a profile's own
    selections are refused with. A profile this person cannot see is left to
    the runtime check beside this one, which says why in its own words.
    """
    if not runtime.config_selections:
        return
    profile = await profiles.get_profile(
        profile_id=runtime.profile_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    if profile is None:
        return
    if (
        profile.protocol is not RuntimeProfileProtocol.AGENT_HOST
        or profile.harness_id is None
    ):
        raise RuntimeProfileValidationError(
            f"{profile.name} takes no configuration selections; they are "
            "for coding agents on a paired computer"
        )
    harnesses = (
        await profiles.host_repository.get_harnesses({profile.harness_id})
        if profiles.host_repository is not None
        else {}
    )
    harness = harnesses.get(profile.harness_id)
    if harness is None:
        raise RuntimeProfileValidationError(
            f"{profile.name} is no longer registered on its computer, so its "
            "configuration cannot be checked"
        )
    validate_agent_host_selections(
        config_options=list(harness.config_options or []),
        selections=dict(runtime.config_selections),
        # The model the run will use: the runtime's own pin, else the
        # profile's. Unpinned altogether is the harness default, whose options
        # are the top-level ones.
        model_name=runtime.model_name or profile.default_model_name,
    )
