"""Two contacts turned out to be one: their channels become one too.

The contacts module announces ``ContactMerged`` in the transaction that merged
them; this moves the merged contact's channel -- its sources, its timeline,
its seats, the conversations working in it -- into the survivor's. A FAST
item: main-database statements only, committed with the item's removal, so it
runs exactly once.

The survivor is whoever the payload's survivor is *now*: when it has since
been merged into a third contact too, that one's channel takes both, rather
than a channel being made for a contact that is no longer anybody.
"""

from __future__ import annotations

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
from app.modules.agent.contracts.contact_conversations import (
    move_channel_conversations,
)
from app.modules.channels.infrastructure.contact_channels import ContactChannels
from app.modules.contacts.contracts import contact_by_id
from app.modules.contacts.contracts.facts import ContactMerged

CHANNELS_CONTACT_MERGED = "channels.contact_merged"


async def fold_channels(ctx: FastContext, payload: ContactMerged) -> None:
    survivor = await contact_by_id(
        ctx.uow, pod_id=payload.pod_id, contact_id=payload.survivor_id
    )
    if survivor is None:
        # Forgotten since, and the merged contact with them: nothing is left.
        return
    channels = ContactChannels(ctx.uow.session)
    fold = await channels.fold(
        pod_id=payload.pod_id, merged_id=payload.merged_id, survivor_id=survivor.id
    )
    if fold is None:
        return
    await move_channel_conversations(
        ctx.uow,
        pod_id=payload.pod_id,
        from_channel_id=fold.merged_channel_id,
        to_channel_id=fold.survivor_channel_id,
    )
    await channels.drop(fold)


def register_contact_merged_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_CONTACT_MERGED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ContactMerged,
    )(fold_channels)


register_contact_merged_work(default_registry)
facts.subscribe(
    ContactMerged,
    kind=CHANNELS_CONTACT_MERGED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channels-contact-merged:{fact.merged_id}",
)
