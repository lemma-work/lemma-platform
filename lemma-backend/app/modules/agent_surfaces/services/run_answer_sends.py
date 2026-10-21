"""Sending a run's answer line to the place its question came from.

A finished run's answer is the agent's line in the channel
(``events/reply_for_run_work``), with a delivery to the place the run was
asked at. This is that delivery's send, which keeps what an answer has always
been on a platform:

* **A live stream finishes in place.** On Slack the answer streamed into one
  message while the model wrote it (``progress_observer``, ``live_display``);
  the answer closes that same message with whatever it does not show yet, and
  the delivery records that message's id -- nothing is posted twice. A stream
  that cannot be closed is taken down and the answer posted as a new message.
* **The run's held files ride the answer** -- one reply on email, where a
  run's outputs fold into the one message the person receives.
* **A failure notice offers a retry** and carries no files: they were shown
  for an answer that never came.
* **Every try of one delivery is the same message** where the platform dedupes
  on a key (Resend).

The answer goes where the run's conversation answers (the message it took up,
in its thread), not where the place last spoke.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.run_replies import run_reply_material
from app.modules.agent_surfaces.domain.adapter_port import IDEMPOTENCY_KEY
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.delivery_outcome import Outcome, of_send
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.live_display import (
    LiveDisplay,
    forget_live_display,
    remainder_after,
    take_live_display,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    discard_display_paths,
)
from app.modules.channels.contracts import ChannelMessage, Delivery, SenderKind
from app.modules.channels.contracts.deliveries import (
    delivery_noted,
    delivery_unnoted,
)

logger = get_logger(__name__)

#: The egress over one unit of work: ``build_surface_egress``, or a stand-in.
type EgressFor = Callable[[SqlAlchemyUnitOfWork], SurfaceEgress]


def send_errors() -> tuple[type[BaseException], ...]:
    """What one send can fail with and still be an outcome rather than a bug.

    A call, read where it is matched (``platform_transport_errors``)."""
    return (AgentSurfaceError, SQLAlchemyError, *platform_transport_errors())


def delivery_key(delivery: Delivery) -> str:
    """The key every try of this delivery is sent under."""
    return f"channel-delivery-{delivery.id}"


def is_run_answer(line: ChannelMessage) -> bool:
    """A run's answer (or its failure notice), posted for it to be delivered."""
    return (
        line.sender.kind is SenderKind.AGENT
        and line.agent_run_id is not None
        and line.conversation_id is not None
    )


class RunAnswerSends:
    def __init__(self, uow_factory: UnitOfWorkFactory, *, egress_for: EgressFor):
        self.uow_factory = uow_factory
        self.egress_for = egress_for

    async def send(
        self,
        *,
        delivery: Delivery,
        line: ChannelMessage,
        platform: str,
        text: str,
        metadata: dict[str, object],
    ) -> Outcome:
        run_id, conversation_id = line.agent_run_id, line.conversation_id
        assert run_id is not None and conversation_id is not None  # is_run_answer
        failed = await self._run_failed(run_id)
        if not failed:
            streamed = await self._finish_stream(
                delivery, run_id, conversation_id, text, platform=platform
            )
            if streamed is not None:
                return streamed
        send_metadata = {**metadata, IDEMPOTENCY_KEY: delivery_key(delivery)}
        if failed:
            send_metadata["retry_action"] = True
        run = None if failed else RunFiles(run_id)
        receipt: DeliveryReceipt | None = None
        raised: BaseException | None = None
        with track_sends() as sends:
            try:
                async with self.uow_factory() as uow:
                    receipt = await self.egress_for(uow).deliver_message(
                        conversation_id=conversation_id,
                        message=text,
                        metadata=send_metadata,
                        attach_files_of=run,
                    )
                    if receipt is not None and receipt.external_message_id:
                        # As it answers: its status report finds the row.
                        await delivery_noted(
                            uow,
                            delivery.id,
                            external_message_id=receipt.external_message_id,
                        )
                    await uow.commit()
            except send_errors() as exc:
                logger.warning(
                    "agent_surfaces.run_answer_sends.send_failed.degraded",
                    delivery_id=str(delivery.id),
                    platform=platform,
                    exc_info=True,
                )
                raised = exc
        if receipt is not None and run is not None:
            # Released with the answer that carried them. One that did not
            # arrive keeps them for its retry, or until they expire.
            await discard_display_paths(conversation_id, run)
        return of_send(
            receipt,
            sends,
            platform=platform,
            raised=raised,
            nothing_sent=(
                "The conversation has nowhere to answer it any more, or the "
                "answer was nothing once its reasoning was stripped."
            ),
        )

    async def _run_failed(self, run_id: UUID) -> bool:
        async with self.uow_factory() as uow:
            material = await run_reply_material(uow, run_id)
        return material is not None and material.status == "FAILED"

    async def _finish_stream(
        self,
        delivery: Delivery,
        run_id: UUID,
        conversation_id: UUID,
        answer: str,
        *,
        platform: str,
    ) -> Outcome | None:
        """Close the run's live stream with the rest of the answer: the answer
        is then that one message, and its id the delivery's. ``None`` when
        there is no stream to close, or closing it failed (it is taken down,
        and the caller posts the answer as a new message).

        A stream that disagrees with the answer is closed as it stands when
        its observer sealed it (it flushed everything the run wrote, so it is
        what the person has been reading); an unsealed one that disagrees (its
        observer died mid-stream) is not trusted.
        """
        display = await take_live_display(run_id)
        if display is None:
            return None
        remainder = remainder_after(display.streamed, answer)
        if remainder is None and display.sealed:
            remainder = ""
        if remainder is not None and await self._close(
            delivery, conversation_id, display, remainder
        ):
            await forget_live_display(run_id)
            logger.info(
                "agent_surfaces.run_answer_sends.stream_finished_in_place.observed",
                delivery_id=str(delivery.id),
                sealed=display.sealed,
            )
            return Outcome.delivered(platform, external_message_id=display.message_id)
        await self._take_down(conversation_id, display)
        await forget_live_display(run_id)
        # The stream is gone: its id no longer says the answer arrived, should
        # this sender die before the answer is posted in its place.
        async with self.uow_factory() as uow:
            await delivery_unnoted(uow, delivery.id)
            await uow.commit()
        return None

    async def _close(
        self,
        delivery: Delivery,
        conversation_id: UUID,
        display: LiveDisplay,
        remainder: str,
    ) -> bool:
        try:
            # Noted before the call: a sender that dies after closing the
            # stream leaves a delivery known to have arrived, not one resent.
            async with self.uow_factory() as uow:
                await delivery_noted(
                    uow, delivery.id, external_message_id=display.message_id
                )
                await uow.commit()
            async with self.uow_factory() as uow:
                return await self.egress_for(uow).progress.finish_with_answer(
                    conversation_id=conversation_id,
                    progress_handle=display.handle,
                    message=remainder,
                    already_streamed=bool(display.streamed.strip()),
                )
        except send_errors():
            logger.warning(
                "agent_surfaces.run_answer_sends.stream_finish_failed.degraded",
                delivery_id=str(delivery.id),
                exc_info=True,
            )
            return False

    async def _take_down(self, conversation_id: UUID, display: LiveDisplay) -> None:
        """Remove a stream the answer will not close, before posting the answer."""
        try:
            async with self.uow_factory() as uow:
                await self.egress_for(uow).progress.clear_progress(
                    conversation_id=conversation_id, progress_handle=display.handle
                )
        except send_errors():
            logger.warning(
                "agent_surfaces.run_answer_sends.stream_take_down_failed.degraded",
                conversation_id=str(conversation_id),
                exc_info=True,
            )


__all__ = [
    "EgressFor",
    "RunAnswerSends",
    "delivery_key",
    "is_run_answer",
    "send_errors",
]
