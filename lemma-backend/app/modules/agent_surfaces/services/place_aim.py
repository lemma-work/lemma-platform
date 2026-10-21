"""Where in its place a line is delivered: the line it answers or the thread it
is in, read from the timeline, then aimed with ``place_threads``.

* An answer in the main timeline answers in place what it replies to, when
  that was heard there: a quoted reply on Telegram or WhatsApp, rather than a
  message the group cannot place.
* A reply in a thread goes to that thread: the root's own event when the root
  was heard there, or the message the root was sent as.
* Anything else at the top of a shared place goes to the place itself.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.place_threads import at_place_level, in_thread
from app.modules.channels.contracts import ChannelMessage, Source, SourceKind
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.contracts.lines import line_by_id


async def aimed_at(
    uow_factory: UnitOfWorkFactory, line: ChannelMessage, source: Source
) -> Source:
    """The place, aimed where in it the line was written."""
    ref = source.delivery_ref
    if ref is None:
        return source
    if line.thread_root_id is None:
        if source.kind is not SourceKind.CHAT:
            return source
        replied = None
        if line.reply_to_id is not None:
            async with uow_factory() as uow:
                replied = await line_by_id(uow, line.reply_to_id)
        if replied is not None and replied.source_id == source.id and replied.reply_ref:
            aimed = dict(replied.reply_ref)
        else:
            aimed = at_place_level(ref, source.platform)
    else:
        async with uow_factory() as uow:
            root = await line_by_id(uow, line.thread_root_id)
            sent_as = await _sent_as(uow, root.id, source.id) if root else None
        if root is not None and root.source_id == source.id and root.reply_ref:
            # Heard there: its own event answers in its thread.
            aimed = dict(root.reply_ref)
        elif sent_as is not None:
            aimed = in_thread(ref, source.platform, root_message_id=sent_as)
        else:
            aimed = at_place_level(ref, source.platform)
    return source.model_copy(update={"delivery_ref": aimed})


async def _sent_as(
    uow: SqlAlchemyUnitOfWork, line_id: UUID, source_id: UUID
) -> str | None:
    """The platform's id for a line written in Lemma and sent to a place."""
    for delivery in await deliveries_of(uow, [line_id]):
        if delivery.source_id == source_id and delivery.external_message_id:
            return delivery.external_message_id
    return None
