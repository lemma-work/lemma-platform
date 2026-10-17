"""Two contacts turned out to be one: their channels become one too.

The contacts module announces ``ContactMerged`` in the transaction that merged
them; this moves the merged contact's channel -- its sources, its timeline,
its seats -- into the survivor's. A FAST item: main-database statements only,
committed with the item's removal, so it runs exactly once.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.contacts.contracts.facts import ContactMerged

CHANNELS_CONTACT_MERGED = "channels.contact_merged"


async def fold_channels(ctx: FastContext, payload: ContactMerged) -> None:
    await ChannelRepository(ctx.uow.session).fold_contact(
        pod_id=payload.pod_id,
        merged_id=payload.merged_id,
        survivor_id=payload.survivor_id,
    )


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
