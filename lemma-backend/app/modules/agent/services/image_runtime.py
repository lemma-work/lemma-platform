"""Which model draws for `generate_image`: the one runtime choice that wants an
image model, kept apart from the chain every other purpose takes.

On the system provider: ``IMAGE_MODEL``, else the first image model it lists.
With no system provider: a workspace provider's image model, and only one.
Never a chat model -- asking one to draw fails, and asking an image model to
chat is refused everywhere else (``RuntimeModelCatalogEntry.draws_only``).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.log.log import get_logger
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.runtime_choice import choose_workspace_runtime
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.domain.vision import ImageModelUnavailableError
from app.modules.agent.services import runtime_system_profiles
from app.modules.agent.services.runtime_system_profiles import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)

logger = get_logger(__name__)

#: The deployment's own provider answers for this server, and workspace
#: providers are not consulted, so only whoever runs it can fix this.
_NO_SYSTEM_DRAWING_MODEL = (
    "This server has no AI model that makes images. Ask whoever runs it to "
    "add an image model to its model provider."
)
_NO_DRAWING_MODEL = (
    "No AI model that makes images is set up. Add an image model to one of the "
    "workspace's providers in Settings → Models."
)


async def resolve_image_runtime(
    *, organization_id: UUID | None, user_id: UUID
) -> ResolvedAgentRuntime:
    """A runtime whose model draws, or ``ImageModelUnavailableError``."""
    from app.modules.agent.services.google_system_profile import (
        system_image_model_name,
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        from app.modules.agent.services.runtime_profile_service import (
            runtime_profile_service_for,
        )

        service = runtime_profile_service_for(uow)
        if not runtime_system_profiles.system_profile_configured():
            if organization_id is None:
                raise ImageModelUnavailableError(_NO_DRAWING_MODEL)
            profiles = await service.list_profiles(
                organization_id=organization_id, user_id=user_id
            )
            runtime = choose_workspace_runtime(
                profiles,
                pod_default=None,
                model_name=None,
                require_vision=False,
                require_image_output=True,
            )
            if runtime is None:
                raise ImageModelUnavailableError(_NO_DRAWING_MODEL)
        else:
            model_name = system_image_model_name()
            if not model_name:
                logger.warning(
                    "agent.image.model_not_configured.refused",
                    operator_hint=(
                        "List an image model in LEMMA_GOOGLE_IMAGE_MODEL_NAMES, or "
                        "set IMAGE_MODEL to one the system provider offers."
                    ),
                )
                raise ImageModelUnavailableError(_NO_SYSTEM_DRAWING_MODEL)
            runtime = AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=model_name,
            )
        try:
            resolved = await service.resolve(
                runtime=runtime,
                organization_id=organization_id,
                user_id=user_id,
                allow_drawing=True,
            )
        except RuntimeUnavailableError:
            logger.warning(
                "agent.image.model_not_offered.refused",
                image_model=runtime.model_name,
                operator_hint="IMAGE_MODEL names a model the system provider does not list.",
            )
            raise ImageModelUnavailableError(
                f"The model set up to make images, {runtime.model_name}, is not "
                "offered here."
            ) from None
    entry = resolved.model
    if entry is None or RuntimeModelCapability.IMAGE_OUTPUT not in entry.capabilities:
        logger.warning(
            "agent.image.model_not_drawing.refused",
            image_model=runtime.model_name,
            operator_hint="IMAGE_MODEL must be listed in LEMMA_GOOGLE_IMAGE_MODEL_NAMES.",
        )
        raise ImageModelUnavailableError(
            f"The model set up to make images, {runtime.model_name}, is not "
            "listed as making images."
        )
    return resolved
