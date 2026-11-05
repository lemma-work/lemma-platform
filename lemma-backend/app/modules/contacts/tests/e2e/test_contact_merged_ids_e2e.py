"""A merged contact's id leads to the person it became, in every verb.

A link or an export kept from before a merge names the absorbed row, which
nobody is shown again. Exporting it exports the person; forgetting it forgets
the person -- not the absorbed row alone, which would leave the person and
everything said with them behind.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.api.dependencies import get_uow_factory
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    open_anonymous_contact,
    open_contact,
    prove_handle,
)
from app.modules.contacts.infrastructure.models import ContactModel

pytestmark = pytest.mark.e2e


async def _merged_pair(
    pod_id: UUID, owner: UUID, email: str
) -> tuple[UUID, UUID, UUID]:
    """A customer with a conversation, and a web visitor who proved the
    customer's email and was merged into them: (customer, visitor,
    conversation)."""
    async with get_uow_factory()() as uow:
        customer = await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value=email,
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        visitor: ContactRef = await open_anonymous_contact(
            uow, pod_id=pod_id, session_id=uuid4()
        )
        conversation = ConversationModel(
            user_id=owner, pod_id=pod_id, contact_id=customer.id
        )
        uow.session.add(conversation)
        await uow.commit()
    async with get_uow_factory()() as uow:
        await prove_handle(
            uow,
            pod_id=pod_id,
            claimant_id=visitor.id,
            kind=IdentityKind.EMAIL,
            value=email,
            strength=IdentityStrength.CODE,
        )
        await uow.commit()
    return customer.id, visitor.id, conversation.id


async def test_exporting_a_merged_id_exports_the_person_it_became(
    authenticated_client, db_session, test_pod, fixed_test_user
):
    pod_id = UUID(test_pod["id"])
    customer, visitor, conversation = await _merged_pair(
        pod_id, UUID(fixed_test_user["id"]), "export-merged@client.example"
    )

    response = await authenticated_client.get(
        f"/pods/{pod_id}/contacts/{visitor}/export"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contact"]["id"] == str(customer)
    assert [item["id"] for item in body["conversations"]] == [str(conversation)]


async def test_forgetting_a_merged_id_forgets_the_person_it_became(
    authenticated_client, db_session, test_pod, fixed_test_user
):
    pod_id = UUID(test_pod["id"])
    customer, visitor, conversation = await _merged_pair(
        pod_id, UUID(fixed_test_user["id"]), "forget-merged@client.example"
    )

    response = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{visitor}")

    assert response.status_code == 204, response.text
    db_session.expire_all()
    left = (
        await db_session.scalars(
            select(ContactModel.id).where(ContactModel.id.in_([customer, visitor]))
        )
    ).all()
    assert left == []
    assert await db_session.get(ConversationModel, conversation) is None
