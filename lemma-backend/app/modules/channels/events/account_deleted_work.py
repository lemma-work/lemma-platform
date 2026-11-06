"""A deleted account's lines stay, under the name "Deleted user".

Subscribed to identity's ``AccountDeleted``. What a person said in a channel
belongs to the conversation it was said in, and the others in it keep it. But
each line carries the name its sender had when they said it, copied onto the
line, so the account being scrubbed is not enough: the copies are renamed too.
A FAST item: one statement on the main database.
"""

from __future__ import annotations

from sqlalchemy import update

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
)
from app.core.infrastructure.work import (
    registry as default_registry,
)
from app.modules.channels.domain.entities import SenderKind
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.identity.contracts.account_facts import (
    DELETED_USER_NAME,
    AccountDeleted,
)

CHANNELS_ACCOUNT_DELETED = "channels.account_deleted"


async def rename_deleted_account_lines(
    ctx: FastContext, payload: AccountDeleted
) -> None:
    await ctx.uow.session.execute(
        update(ChannelMessageModel)
        .where(
            ChannelMessageModel.sender_user_id == payload.user_id,
            ChannelMessageModel.sender_kind == SenderKind.MEMBER.value,
            ChannelMessageModel.sender_name.is_distinct_from(DELETED_USER_NAME),
        )
        .values(sender_name=DELETED_USER_NAME)
    )


def register_account_deleted_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_ACCOUNT_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AccountDeleted,
    )(rename_deleted_account_lines)


register_account_deleted_work(default_registry)
facts.subscribe(
    AccountDeleted,
    kind=CHANNELS_ACCOUNT_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channels-account-deleted:{fact.user_id}",
)
