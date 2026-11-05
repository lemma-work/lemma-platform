"""A contact proved to be one of the pod's members: their lines are the member's.

The contacts module announces ``ContactBecameMember`` in the transaction that
took the handle from the contact; this moves what the contact said in the
pod's groups -- their lines, their seats -- onto the member, so the group
reads as the member having said it. A FAST item: main-database statements
only, committed with the item's removal, so it runs exactly once.
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
from app.modules.channels.infrastructure.contact_channels import ContactChannels
from app.modules.contacts.contracts.facts import ContactBecameMember

CHANNELS_CONTACT_BECAME_MEMBER = "channels.contact_became_member"


async def move_to_member(ctx: FastContext, payload: ContactBecameMember) -> None:
    await ContactChannels(ctx.uow.session).became_member(
        pod_id=payload.pod_id, contact_id=payload.contact_id, user_id=payload.user_id
    )


def register_contact_became_member_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_CONTACT_BECAME_MEMBER,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ContactBecameMember,
    )(move_to_member)


register_contact_became_member_work(default_registry)
facts.subscribe(
    ContactBecameMember,
    kind=CHANNELS_CONTACT_BECAME_MEMBER,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channels-contact-became-member:{fact.contact_id}",
)
