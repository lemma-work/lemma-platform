"""Two contacts turned out to be one: their conversations follow the survivor.

The contacts module moves what it keeps when it merges two contacts, and
announces ``ContactMerged`` in that transaction; this repoints the
conversations that named the merged one. A FAST item: one statement on the main
database, committed with the item's removal, so it runs exactly once.

Until it runs, a run in such a conversation still answers the right person:
the merged contact's id leads to the survivor (``contacts.contact_by_id``).
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
from app.modules.contacts.contracts import contact_by_id
from app.modules.contacts.contracts.facts import ContactMerged

AGENT_CONTACT_MERGED = "agent.contact_merged"


async def repoint_conversations(ctx: FastContext, payload: ContactMerged) -> None:
    from app.modules.agent.application.channels.fold import FoldConversations

    # Whoever the survivor is now: merged into a third contact since, its
    # conversations went there, and these follow them.
    survivor = await contact_by_id(
        ctx.uow, pod_id=payload.pod_id, contact_id=payload.survivor_id
    )
    if survivor is None:
        return
    await FoldConversations.build(ctx.uow).merge_contact(
        pod_id=payload.pod_id,
        merged_id=payload.merged_id,
        survivor_id=survivor.id,
    )


def register_contact_merged_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_CONTACT_MERGED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ContactMerged,
    )(repoint_conversations)


register_contact_merged_work(default_registry)
facts.subscribe(
    ContactMerged,
    kind=AGENT_CONTACT_MERGED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"agent-contact-merged:{fact.merged_id}",
)
