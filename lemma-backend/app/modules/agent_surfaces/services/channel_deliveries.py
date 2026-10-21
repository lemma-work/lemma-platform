"""Carrying a channel line out of Lemma, one delivery at a time.

Channels plans a delivery for every send outside Lemma -- a line written in
Lemma for one of the channel's places, a push to a member who hears of it --
and announces it (``ChannelDeliveryDue``). This takes one: claims it, sends
it, and records what became of it on the same row -- with the platform's id
for the message, noted as soon as the platform answers, which is what the
platform's later status report about it names (``whatsapp_delivery_status``).
What a try ends as is ``delivery_outcome``'s rule: retried only when the retry
cannot be a second copy. One whose sender died mid-send is sent again where
the platform dedupes on the delivery's key, and otherwise settled without
sending it again (``channels.contracts.deliveries``). An error before any
platform call is recorded as a failure to try again, before it propagates.

Each send is bounded (``SEND_TIMEOUT``), below how long a claim is held, so a
slow platform is a failure tried again rather than a claim that lapses.

What goes to a place:

* **A run's answer** goes where its conversation answers, finishing a live
  stream in place where there is one (``run_answer_sends``).
* **Any other line** goes where it was written: a reply in a thread to the
  platform thread its root is in; a line at the top of a shared place to the
  place itself, not the thread of whoever spoke last (``place_threads``).

Every line to a contact's place -- a member's, an agent's answer, an
acknowledgement -- is first held to the contact's outreach rules, which
``contacts`` owns (``contacts.contracts.outreach``): opt-out first, then a
handle, then the platform's window (``contact_reach``), then, for a member's
line, the contact's day. Email to a contact carries a line, and
``List-Unsubscribe`` headers, saying how to stop. **A web chat** has nobody to
hand it to: it waits in their chat for their next visit.

What a member wrote to a contact goes into their conversation too, marked as a
member's (``append_follow_up``), so the agent answering them next knows a
person said it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from opentelemetry import metrics
from redis.asyncio import Redis

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.domain.adapter_port import IDEMPOTENCY_KEY
from app.modules.agent_surfaces.domain.deliveries import SEND_TIMEOUT
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.channel_lines import WEB
from app.modules.agent_surfaces.services.contact_reach import reach_for
from app.modules.agent_surfaces.services.contact_refusals import (
    ContactRefused,
    Ready,
    outreach_kind,
)
from app.modules.agent_surfaces.services.cold_open_sends import (
    cold_open_surface,
    send_cold_open,
)
from app.modules.agent_surfaces.services.delivery_outcome import (
    RESENDS_SAFELY,
    Outcome,
    Result,
    of_send,
    raised_before_recording,
    timed_out,
)
from app.modules.agent_surfaces.services.push_sends import PushesFor, push_outcome
from app.modules.agent_surfaces.services.surface_problems import heed_send
from app.modules.agent_surfaces.services.place_aim import aimed_at
from app.modules.agent_surfaces.services.run_answer_sends import (
    EgressFor,
    RunAnswerSends,
    delivery_key,
    is_run_answer,
    send_errors,
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Delivery,
    DeliveryReasonCode as Code,
    SenderKind,
    Source,
    channel_by_id,
    source_by_id,
)
from app.modules.channels.contracts.deliveries import (
    announce_next_for_target,
    claim_delivery,
    delivery_dead,
    delivery_failed,
    delivery_folded,
    delivery_interrupted,
    delivery_noted,
    delivery_sent,
    delivery_skipped,
    lapsed_delivery,
    reclaim_delivery,
)
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.places import conversation_at
from app.modules.contacts.contracts.outreach import (
    UNSUBSCRIBE_TOKEN_KEY,
    count_against_today,
    may_send,
)

logger = get_logger(__name__)

#: A line to a web chat waits there: nothing tells a visitor who left.
WAITS_IN_WEB_CHAT = (
    "In their web chat: they see it when they next open it. Nothing tells them "
    "sooner -- the web chat sends no email."
)

meter = metrics.get_meter(__name__)
deliveries_done = meter.create_counter(
    "lemma.channels.deliveries",
    description="Channel deliveries tried, by platform, target and result.",
)
delivery_seconds = meter.create_histogram(
    "lemma.channels.delivery_seconds",
    unit="s",
    description="One channel delivery: policy, the platform's send, its answer.",
)


@dataclass(frozen=True, slots=True)
class _Work:
    delivery: Delivery
    line: ChannelMessage
    channel: Channel


class ChannelDeliveries:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        egress_for: EgressFor,
        pushes_for: PushesFor,
        redis: Redis | None = None,
        send_timeout: float = SEND_TIMEOUT.total_seconds(),
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_for = egress_for
        self.pushes_for = pushes_for
        #: Where a contact's day is counted; the shared client when ``None``.
        self.redis = redis
        self.send_timeout = send_timeout
        self.answers = RunAnswerSends(uow_factory, egress_for=egress_for)

    async def deliver(self, delivery_id: UUID) -> None:
        """Send one delivery, if it is due, and record the outcome on it."""
        work = await self._claim(delivery_id)
        if work is None:
            return
        started = time.monotonic()
        with track_sends() as sends:
            try:
                async with asyncio.timeout(self.send_timeout):
                    outcome = await self._outcome(work)
            except TimeoutError:
                logger.warning(
                    "agent_surfaces.channel_deliveries.send_timed_out.degraded",
                    delivery_id=str(delivery_id),
                    timeout_seconds=self.send_timeout,
                )
                outcome = timed_out(sends, timeout=self.send_timeout)
            except asyncio.CancelledError:
                # The worker is stopping: the claim lapses, and is settled
                # (or sent again where that is safe) once its lease is over.
                raise
            except BaseException as exc:
                # Recorded before it propagates: a claim left behind would be
                # settled as interrupted -- given up -- when nothing may have
                # been sent at all.
                await self._record(work.delivery, raised_before_recording(sends, exc))
                raise
        attributes = {
            "platform": outcome.platform or "NONE",
            "result": outcome.result.value,
            "target": "place" if work.delivery.source_id else "member",
        }
        deliveries_done.add(1, attributes)
        delivery_seconds.record(time.monotonic() - started, attributes)
        await self._record(work.delivery, outcome)

    async def _outcome(self, work: _Work) -> Outcome:
        if work.line.is_deleted:
            return Outcome.skipped(
                "The line was deleted before it went.", code=Code.LINE_GONE
            )
        if work.delivery.source_id is not None:
            return await self._to_place(work, work.delivery.source_id)
        return await self._to_member(work)

    async def _claim(self, delivery_id: UUID) -> _Work | None:
        async with self.uow_factory() as uow:
            delivery = await claim_delivery(uow, delivery_id)
            if delivery is None:
                delivery = await self._after_a_lapse(uow, delivery_id)
            if delivery is None:
                await uow.commit()
                return None
            line = await line_by_id(uow, delivery.message_id)
            channel = await channel_by_id(uow, line.channel_id) if line else None
            if line is None or channel is None:
                await delivery_skipped(
                    uow, delivery.id, code=Code.LINE_GONE, reason="The line is gone."
                )
                await uow.commit()
                return None
            await uow.commit()
        return _Work(delivery=delivery, line=line, channel=channel)

    async def _after_a_lapse(
        self, uow: SqlAlchemyUnitOfWork, delivery_id: UUID
    ) -> Delivery | None:
        """A claim whose sender died before recording what happened. Sent
        again where the place's platform dedupes a resend by the delivery's
        key -- a deploy mid-send then costs nothing -- and otherwise settled,
        never sent again (see ``channels.infrastructure.deliveries``)."""
        lapsed = await lapsed_delivery(uow, delivery_id)
        if lapsed is None:
            return None
        if await self._resends_safely(uow, lapsed):
            reclaimed = await reclaim_delivery(uow, delivery_id)
            if reclaimed is not None:
                logger.info(
                    "agent_surfaces.channel_deliveries.send_resumed.observed",
                    delivery_id=str(delivery_id),
                    attempts=reclaimed.attempts,
                )
                return reclaimed
        settled = await delivery_interrupted(uow, delivery_id)
        if settled is not None:
            logger.warning(
                "agent_surfaces.channel_deliveries.send_interrupted.degraded",
                delivery_id=str(delivery_id),
                status=settled.status.value,
                platform=settled.platform,
            )
            await announce_next_for_target(uow, settled)
        return None

    @staticmethod
    async def _resends_safely(uow: SqlAlchemyUnitOfWork, delivery: Delivery) -> bool:
        """Whether this delivery's place is on a platform that dedupes a resend
        by its key. A push's platform is chosen as it is sent, so it is not."""
        if delivery.source_id is None or delivery.external_message_id is not None:
            return False
        source = await source_by_id(uow, delivery.source_id)
        return source is not None and source.platform in RESENDS_SAFELY

    # -- to a place -------------------------------------------------------------

    async def _to_place(self, work: _Work, source_id: UUID) -> Outcome:
        """The line, to the outside place it was written for, as the pod's bot."""
        line, channel = work.line, work.channel
        if line.members_only:
            return Outcome.skipped(
                "A note for the pod's members goes nowhere else.",
                code=Code.MEMBERS_ONLY,
            )
        answer = is_run_answer(line)
        async with self.uow_factory() as uow:
            source = await source_by_id(uow, source_id)
            conversation_id = (
                line.conversation_id
                if answer
                else await conversation_at(uow, source.id)
                if source is not None
                else None
            )
        if source is None or source.channel_id != channel.id:
            return Outcome.skipped(
                "That place is no longer this channel's.", code=Code.PLACE_GONE
            )
        platform = source.platform
        if platform != WEB and source.surface_id is None:
            # The bot that reached it was removed: trying again reaches nobody.
            return Outcome.skipped(
                "The bot that reached this place is no longer connected.",
                code=Code.PLACE_GONE,
                platform=platform,
            )
        outcome = await self._to_source(work, source, conversation_id)
        await heed_send(self.uow_factory, channel, source, outcome)
        return outcome

    async def _to_source(
        self, work: _Work, source: Source, conversation_id: UUID | None
    ) -> Outcome:
        line, channel = work.line, work.channel
        platform = source.platform
        answer = is_run_answer(line)
        try:
            ready = await self._ready(channel, source, line)
        except ContactRefused as refused:
            return refused.outcome(platform)
        if conversation_id is None:
            # Nobody there has spoken to the bot: only mail can write first.
            return await self._cold_open(work, source, ready)
        if platform == WEB:
            # Nobody to hand it to: it waits in their web chat for the next visit.
            await self._to_their_conversation(channel, conversation_id, line, None)
            return Outcome.delivered(platform, reason=WAITS_IN_WEB_CHAT)
        if answer:
            return await self.answers.send(
                delivery=work.delivery,
                line=line,
                platform=platform,
                text=ready.text,
                metadata=ready.metadata,
            )
        try:
            await self._count_today(ready)
        except ContactRefused as refused:
            return refused.outcome(platform)
        receipt, outcome = await self._send(
            work.delivery,
            conversation_id,
            await aimed_at(self.uow_factory, line, source),
            text=ready.text,
            metadata=ready.metadata,
        )
        await self._to_their_conversation(
            channel, conversation_id, line, receipt is not None
        )
        return outcome

    async def _ready(
        self, channel: Channel, source: Source, line: ChannelMessage
    ) -> Ready:
        """The text that goes, once a contact's rules allow it to; or a refusal
        (``ContactRefused``). The contact's day is counted later, once the
        line can actually go (``_count_today``)."""
        if channel.kind is not ChannelKind.CONTACT or channel.contact_id is None:
            return Ready(text=line.text)
        reach = reach_for(
            source.platform,
            address=source.external_user_id,
            last_inbound_at=source.last_inbound_at,
        )
        if reach is None:
            # A web chat: nobody is written to until they come back.
            return Ready(text=line.text)
        to, window = reach
        async with self.uow_factory() as uow:
            verdict = await may_send(
                uow,
                pod_id=channel.pod_id,
                contact_id=channel.contact_id,
                to=to,
                kind=outreach_kind(line),
                at=datetime.now(timezone.utc),
                window=window,
            )
        if not verdict.allowed:
            raise ContactRefused.of(verdict)
        metadata: dict[str, object] = {}
        if verdict.unsubscribe_token is not None:
            metadata[UNSUBSCRIBE_TOKEN_KEY] = verdict.unsubscribe_token
        return Ready(
            text=line.text + verdict.footer,
            metadata=metadata,
            day_of=(
                (channel.contact_id, line.id)
                if line.sender.kind is SenderKind.MEMBER
                else None
            ),
        )

    async def _count_today(self, ready: Ready) -> None:
        """Count a member's line against the contact's day, just before it is
        handed to the platform: a line that could not have gone anyway (no
        thread to send into, a bot that cannot write first) spends nothing. A
        line counts once: a retry is the same line."""
        if ready.day_of is None:
            return
        contact_id, line_id = ready.day_of
        counted = await count_against_today(
            contact_id=contact_id, line_id=line_id, redis=self.redis
        )
        if not counted.allowed:
            raise ContactRefused.of(counted)

    async def _cold_open(self, work: _Work, source: Source, ready: Ready) -> Outcome:
        """A line to an email address nobody has written from yet -- a
        member's first word to a contact: a thread seeded so the reply comes
        back to this place (``cold_email_thread``). A later line before they
        reply goes into the same thread, not a new one each time."""
        platform = source.platform
        surface = await cold_open_surface(self.uow_factory, self.egress_for, source)
        if surface is None:
            return Outcome.skipped(
                "The bot has not talked with anybody there yet.",
                code=Code.CANNOT_WRITE_FIRST,
                platform=platform,
            )
        try:
            await self._count_today(ready)
        except ContactRefused as refused:
            return refused.outcome(platform)
        return await send_cold_open(
            self.uow_factory,
            self.egress_for,
            delivery=work.delivery,
            line=work.line,
            channel=work.channel,
            source=source,
            surface=surface,
            text=ready.text,
            metadata=ready.metadata,
        )

    async def _send(
        self,
        delivery: Delivery,
        conversation_id: UUID,
        source: Source,
        *,
        text: str,
        metadata: dict[str, object],
    ) -> tuple[DeliveryReceipt | None, Outcome]:
        """Hand it to the platform at its place: its receipt, and the outcome.
        The platform's id is noted as it answers, so its report about the
        message (a WhatsApp failure) finds the row however soon it comes."""
        receipt: DeliveryReceipt | None = None
        raised: BaseException | None = None
        with track_sends() as sends:
            try:
                async with self.uow_factory() as uow:
                    receipt = await self.egress_for(uow).deliver_message(
                        conversation_id=conversation_id,
                        message=text,
                        metadata={**metadata, IDEMPOTENCY_KEY: delivery_key(delivery)},
                        at=source,
                    )
                    if receipt is not None and receipt.external_message_id:
                        await delivery_noted(
                            uow,
                            delivery.id,
                            external_message_id=receipt.external_message_id,
                        )
                    await uow.commit()
            except send_errors() as exc:
                logger.warning(
                    "agent_surfaces.channel_deliveries.send_failed.degraded",
                    platform=source.platform,
                    error_type=type(exc).__name__,
                )
                raised = exc
        return receipt, of_send(
            receipt,
            sends,
            platform=source.platform,
            raised=raised,
            nothing_sent=(
                "Nothing there to send it into, or nothing left to send once it "
                "was cleaned up."
            ),
        )

    async def _to_their_conversation(
        self,
        channel: Channel,
        conversation_id: UUID,
        line: ChannelMessage,
        delivered: bool | None,
    ) -> None:
        """A contact's conversation hears what a member wrote them, as theirs:
        once per line, its ``delivered`` brought up to date by a retry."""
        if channel.kind is not ChannelKind.CONTACT or line.sender.user_id is None:
            return
        async with self.uow_factory() as uow:
            await append_follow_up(
                uow,
                conversation_id=conversation_id,
                line_id=line.id,
                sent_by_user_id=line.sender.user_id,
                delivered=delivered,
            )
            await uow.commit()

    # -- to a member ------------------------------------------------------------

    async def _to_member(self, work: _Work) -> Outcome:
        return await push_outcome(
            self.uow_factory,
            self.pushes_for,
            delivery=work.delivery,
            channel=work.channel,
            line=work.line,
        )

    async def _record(self, delivery: Delivery, outcome: Outcome) -> None:
        reason = outcome.reason or "Not sent."
        code = outcome.code or Code.TRANSIENT
        status = outcome.provider_status
        async with self.uow_factory() as uow:
            match outcome.result:
                case Result.SENT:
                    await delivery_sent(
                        uow,
                        delivery.id,
                        platform=outcome.platform or "LEMMA",
                        external_message_id=outcome.external_message_id,
                        provider_status=status,
                        reason=outcome.reason,
                    )
                case Result.SKIPPED if outcome.hold_for is not None:
                    await delivery_folded(
                        uow, delivery, reason=reason, hold_for=outcome.hold_for
                    )
                case Result.SKIPPED:
                    await delivery_skipped(
                        uow,
                        delivery.id,
                        code=code,
                        reason=reason,
                        platform=outcome.platform,
                        provider_status=status,
                    )
                case Result.DEAD:
                    await delivery_dead(
                        uow,
                        delivery.id,
                        code=code,
                        reason=reason,
                        platform=outcome.platform,
                        provider_status=status,
                    )
                case Result.FAILED:
                    await delivery_failed(
                        uow,
                        delivery,
                        code=code,
                        reason=reason,
                        platform=outcome.platform,
                        retry_after=outcome.retry_after,
                        provider_status=status,
                    )
            # The next line to this target was waiting on this one.
            await announce_next_for_target(uow, delivery)
            await uow.commit()
        logger.info(
            "agent_surfaces.channel_deliveries.delivered.observed",
            platform=outcome.platform,
            result=outcome.result.value,
            has_platform_id=outcome.external_message_id is not None,
        )
