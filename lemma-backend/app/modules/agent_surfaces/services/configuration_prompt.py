"""What a person is shown when an in-platform event cannot be pinned to a pod.

Two ways that happens: the person may edit nothing here, or they may edit
several pods and have not said which. Neither is an error -- both have a screen,
the App Home or the channel they just added the app to -- and the only thing
that differs between the two is what is said.

A function rather than a method on ``AppEventHandler``: it needs nothing of the
handler but a session to give back during the platform call, and the handler is
at the file-size ceiling.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
)


async def prompt_for_configuration(
    session: AsyncSession | None,
    *,
    adapter: SurfacePlatformAdapterPort,
    parsed: ParsedSurfaceLifecycleEvent,
    actor_external_user_id: str,
    credentials: dict[str, object],
    surface_choices: list[tuple[str, str]] | None,
) -> None:
    """Ask which pod, or explain why nothing can be shown.

    ``surface_choices`` of None means the actor is not authorized anywhere,
    which is the only thing that changes between the two messages.

    The actor is a parameter rather than read back off ``parsed``, where it
    is ``str | None``: the caller has already refused an event without one,
    and taking it here is how that guarantee crosses the boundary.
    """
    no_access = surface_choices is None
    if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
        async with connection_released(session):
            await adapter.publish_home_view(
                credentials=credentials,
                user_id=actor_external_user_id,
                pod_name=None,
                # No surface has been chosen on this path, so there is no
                # agent whose name this could be. It was omitted entirely
                # until the port declared the operation, and `agent_name`
                # has no default on any implementation -- so the one screen
                # that tells somebody they have no access to a connected pod
                # raised `TypeError` and rendered nothing at all.
                agent_name=DEFAULT_RESPONDER_NAME,
                channel_ids=[],
                agents=[],
                apps=[],
                surface_choices=surface_choices,
                access_message=(
                    "You need access to a connected Lemma pod before this app can show agents or settings."
                    if no_access
                    else None
                ),
            )
        return

    if (
        parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
        and parsed.external_channel_id
    ):
        async with connection_released(session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=parsed.external_channel_id,
                user_id=actor_external_user_id,
                surface_choices=surface_choices,
                configuration_error=(
                    "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                    if no_access
                    else None
                ),
            )
