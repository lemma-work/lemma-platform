"""Proving a handle against a real pod: attach, merge, and two proofs at once.

The rules are pinned without a database in ``unit/test_merging``; these pin
what carrying them out does to the rows -- and that two people proving at the
same moment end up as one contact each, not as a half-merged pair.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.work.models import WorkItem
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    ProofTooWeak,
    contact_by_id,
    open_anonymous_contact,
    open_contact,
    prove_handle,
)
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)

pytestmark = pytest.mark.e2e


async def _anonymous(pod_id: UUID) -> ContactRef:
    """A browser that wrote something, with no session row behind it.

    The session's id only names the handle; nothing here reads the session.
    """
    async with get_uow_factory()() as uow:
        contact = await open_anonymous_contact(uow, pod_id=pod_id, session_id=uuid4())
        await uow.commit()
    return contact


async def _customer(pod_id: UUID, email: str) -> ContactRef:
    async with get_uow_factory()() as uow:
        contact = await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value=email,
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        await uow.commit()
    return contact


async def _prove(
    pod_id: UUID,
    claimant: ContactRef | None,
    email: str,
    strength: IdentityStrength = IdentityStrength.CODE,
) -> ContactRef:
    async with get_uow_factory()() as uow:
        proven = await prove_handle(
            uow,
            pod_id=pod_id,
            claimant_id=claimant.id if claimant else None,
            kind=IdentityKind.EMAIL,
            value=email,
            strength=strength,
        )
        await uow.commit()
    return proven


async def _handles(db_session, contact_id: UUID) -> set[str]:
    db_session.expire_all()
    return set(
        (
            await db_session.execute(
                select(ContactIdentityModel.kind).where(
                    ContactIdentityModel.contact_id == contact_id
                )
            )
        ).scalars()
    )


async def test_a_handle_nobody_holds_joins_the_contact_who_proved_it(
    db_session, test_pod
):
    pod_id = UUID(test_pod["id"])
    visitor = await _anonymous(pod_id)
    assert visitor.is_anonymous

    proven = await _prove(pod_id, visitor, "new-person@client.example")

    assert proven.id == visitor.id
    assert not proven.is_anonymous
    assert await _handles(db_session, visitor.id) == {"WEB", "EMAIL"}


async def test_a_held_handle_merges_the_claimant_into_its_holder_and_says_so(
    db_session, test_pod
):
    pod_id = UUID(test_pod["id"])
    customer = await _customer(pod_id, "dana@client.example")
    visitor = await _anonymous(pod_id)

    proven = await _prove(pod_id, visitor, "dana@client.example")

    assert proven.id == customer.id
    assert proven.display_name == "Dana"
    db_session.expire_all()
    merged = await db_session.get(ContactModel, visitor.id)
    assert merged is not None and merged.merged_into_id == customer.id
    assert await _handles(db_session, customer.id) == {"EMAIL", "WEB"}
    assert await _handles(db_session, visitor.id) == set()
    # The old id still leads to the person.
    async with get_uow_factory()() as uow:
        followed = await contact_by_id(uow, pod_id=pod_id, contact_id=visitor.id)
    assert followed is not None and followed.id == customer.id
    # And the agent is told to move the conversations, in the merge's commit.
    announced = (
        await db_session.execute(
            select(WorkItem.payload).where(WorkItem.kind == "agent.contact_merged")
        )
    ).scalars()
    assert {
        "pod_id": str(pod_id),
        "survivor_id": str(customer.id),
        "merged_id": str(visitor.id),
    } in [dict(payload) for payload in announced]


async def test_a_handle_another_contact_holds_is_not_taken_on_a_session(
    db_session, test_pod
):
    pod_id = UUID(test_pod["id"])
    await _customer(pod_id, "kept@client.example")
    visitor = await _anonymous(pod_id)

    with pytest.raises(ProofTooWeak):
        await _prove(
            pod_id, visitor, "kept@client.example", strength=IdentityStrength.SESSION
        )

    db_session.expire_all()
    still = await db_session.get(ContactModel, visitor.id)
    assert still is not None and still.merged_into_id is None


async def test_two_visitors_proving_one_new_address_at_once_end_as_one_contact(
    db_session, test_pod
):
    """The race a code sent twice invites: both attach, one wins, the other
    finds it held -- and merges into the one that won, rather than failing."""
    pod_id = UUID(test_pod["id"])
    first, second = await _anonymous(pod_id), await _anonymous(pod_id)

    proven = await asyncio.gather(
        _prove(pod_id, first, "both@client.example"),
        _prove(pod_id, second, "both@client.example"),
    )

    assert proven[0].id == proven[1].id
    survivor = proven[0].id
    loser = second.id if survivor == first.id else first.id
    db_session.expire_all()
    merged = await db_session.get(ContactModel, loser)
    assert merged is not None and merged.merged_into_id == survivor
    assert await _handles(db_session, survivor) == {"WEB", "EMAIL"}
    holders = (
        await db_session.execute(
            select(ContactIdentityModel.contact_id).where(
                ContactIdentityModel.value == "both@client.example"
            )
        )
    ).scalars()
    assert list(holders) == [survivor]


async def test_renaming_a_merged_contact_renames_the_person_it_leads_to(
    authenticated_client, db_session, test_pod
):
    """A link kept from before a merge renames the contact it shows, not the
    absorbed row nobody sees again."""
    pod_id = UUID(test_pod["id"])
    customer = await _customer(pod_id, "renamed@client.example")
    visitor = await _anonymous(pod_id)
    await _prove(pod_id, visitor, "renamed@client.example")

    response = await authenticated_client.patch(
        f"/pods/{pod_id}/contacts/{visitor.id}", json={"display_name": "Dana Ross"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(customer.id)
    assert response.json()["display_name"] == "Dana Ross"
    db_session.expire_all()
    survivor = await db_session.get(ContactModel, customer.id)
    assert survivor is not None and survivor.display_name == "Dana Ross"
