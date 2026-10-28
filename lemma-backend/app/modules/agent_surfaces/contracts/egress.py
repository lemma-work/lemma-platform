"""What an agent run sends out to the chat surface it is answering on.

Six operations, replacing `app/composition/agent_surface_runtime.py`. That file
existed because `agent` importing `agent_surfaces` would have closed a loop; the
loop is cut, so this is an ordinary contract
and can say what it takes and returns. Every signature on the shim was `object`
or `list[object]` -- not because the types were unknown, but because naming them
in the composition root would have put a third module's paths into `agent`'s
build. `agent/contracts` publishes `DisplayResourceRequest`, so it is named
here.

The direction is one-way: `agent` decides what to send and this delivers it.
What a platform *is* -- the capability lookups and the prompt text derived from
them -- is `contracts/platforms.py`.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
service and adapter layers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.tools import DisplayResourceRequest
from app.modules.agent.contracts.tools import ToolSpec
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    SurfaceEventMetadata,
)

logger = get_logger(__name__)


async def hold_display_for_one_reply(
    conversation_id: UUID, path: str, agent_run_id: UUID | None = None
) -> bool:
    """Keep a displayed pod file until this run's single reply goes out.

    Held in Redis, not in this process: a remote harness's tool calls execute in
    an API replica while the reply is sent by the worker's run observer. Held for
    the run that showed it, so no other run's reply can carry it.
    """
    from app.modules.agent_surfaces.services.pending_envelope import (
        RunFiles,
        remember_display_path,
    )

    return await remember_display_path(conversation_id, RunFiles(agent_run_id), path)


def parse_surface_event_metadata(
    payload: Mapping[str, object],
) -> SurfaceEventMetadata:
    """The surface metadata a conversation was started with, typed."""
    from pydantic import TypeAdapter

    return TypeAdapter(SurfaceEventMetadata).validate_python(payload)


async def build_surface_tools(
    uow_factory: UnitOfWorkFactory,
    *,
    platform: str,
    surface_id: UUID | None,
    conversation_kind: str | None,
) -> list[ToolSpec]:
    """The tools the platform a run talks over adds to it, as specs."""
    from app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory import (
        SurfacePlatformToolFactory,
    )

    return await SurfacePlatformToolFactory(uow_factory).build_tools(
        platform=platform, surface_id=surface_id, conversation_kind=conversation_kind
    )


async def deliver_display_resource(
    *,
    conversation_id: UUID,
    request: DisplayResourceRequest,
    tool_call_id: str | None,
    tool_output: object | None,
) -> bool:
    """Show a resource on the conversation's surface. False when it has none.

    Never raises: delivery is best-effort and must not abort the run.
    """
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_display_resource_to_surface,
    )

    return await deliver_display_resource_to_surface(
        conversation_id=conversation_id,
        request=request,
        tool_call_id=tool_call_id,
        tool_output=tool_output,
    )


async def deliver_voice_note(*, conversation_id: UUID, file_path: str) -> bool:
    """Send a spoken reply as a voice note. False when the surface takes none."""
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_voice_note_to_surface,
    )

    return await deliver_voice_note_to_surface(
        conversation_id=conversation_id,
        file_path=file_path,
    )


#: Said when the worker job carrying out an approved action failed. It may have
#: got partway, so the person is asked to look before asking again rather than
#: being invited to approve it a second time.
APPROVED_ACTION_FAILED_NOTICE = (
    "Something went wrong while carrying out what you approved, so it may not "
    "have happened. Check before asking me to try again."
)


async def report_approved_action_failed(
    *, uow_factory: UnitOfWorkFactory, conversation_id: UUID
) -> bool:
    """Tell the conversation's platform that an approved action may not have run.

    The tap that approved it was already answered "Approved — running it now",
    so a job failing afterwards is otherwise a promise nobody hears broken.
    False when the conversation is not on a platform, or the notice could not
    be sent. Never raises: the caller is already handling a failure, and this
    must not replace it with its own.
    """
    from redis.exceptions import RedisError
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.agent_surfaces.composition import build_surface_egress
    from app.modules.agent_surfaces.platforms.common import platform_transport_errors

    try:
        async with uow_factory() as uow:
            return await build_surface_egress(uow).send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=APPROVED_ACTION_FAILED_NOTICE,
            )
    except (SQLAlchemyError, RedisError, *platform_transport_errors()):
        logger.warning(
            "agent_surfaces.egress.approval_failure_notice_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return False


async def retire_cards_answered_in_lemma(
    *,
    conversation_id: UUID,
    tool_call_id: str,
    uow_factory: UnitOfWorkFactory | None = None,
) -> None:
    """Take the buttons off the platform cards of a pause answered in Lemma.

    For the web app's decision endpoint, once the decision has committed: a
    tap and a typed reply retire their own cards, and an answer given in the
    web app reaches neither path. The cards then read "Answered." Nothing for
    a conversation that is not on a platform. Never raises.
    """
    from app.core.infrastructure.db.session import get_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent_surfaces.composition import build_surface_egress
    from app.modules.agent_surfaces.services.card_retirement import (
        retire_cards_answered_in_lemma as retire,
    )

    await retire(
        uow_factory=uow_factory or SessionUnitOfWorkFactory(get_session_maker()),
        egress_factory=build_surface_egress,
        conversation_id=conversation_id,
        tool_call_ids=[tool_call_id],
    )


def build_progress_observer(
    *, uow_factory: UnitOfWorkFactory
) -> SurfaceAgentRunProgressObserver:
    """An observer that streams a run's progress to the surface watching it.

    Builds its own egress objects rather than taking them. The caller is
    `agent`, which has no business knowing how a surfaces object is wired -- and
    the wiring it used to pass lived on `AppWorkerContext`, so `app/core` had to
    know too. Both copies had already drifted from `composition`, which is what
    this uses.

    It takes `SurfaceEgress`, not the ingress service. An observer watching a
    run has nothing to say about inbound webhooks, routing or configuration; it
    only got them because all thirteen outbound verbs were mixed onto the one
    object that handled those too.
    """
    from app.modules.agent_surfaces.composition import build_surface_egress
    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )

    return SurfaceAgentRunProgressObserver(
        uow_factory=uow_factory,
        egress_factory=build_surface_egress,
    )


__all__ = [
    "APPROVED_ACTION_FAILED_NOTICE",
    "build_progress_observer",
    "build_surface_tools",
    "deliver_display_resource",
    "deliver_voice_note",
    "hold_display_for_one_reply",
    "parse_surface_event_metadata",
    "report_approved_action_failed",
    "retire_cards_answered_in_lemma",
]
