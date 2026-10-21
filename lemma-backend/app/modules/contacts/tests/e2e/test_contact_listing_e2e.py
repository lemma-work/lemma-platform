"""Finding a contact, and what they are called.

A member looking for somebody types what they know of them -- a name, part of
an email address, a number as it is said aloud -- and a contact nobody named
is called by the handle they are known by, not left blank.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.api.dependencies import get_uow_factory
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    open_anonymous_contact,
    open_contact,
)

pytestmark = pytest.mark.e2e


async def _contacts(pod_id: UUID) -> None:
    async with get_uow_factory()() as uow:
        await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value="dana.reyes@client.example",
            strength=IdentityStrength.CHANNEL,
            display_name=None,
        )
        await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.PHONE,
            value="+1 555 010 4477",
            strength=IdentityStrength.CHANNEL,
            display_name="Omar",
        )
        await open_anonymous_contact(uow, pod_id=pod_id, session_id=uuid4())
        await uow.commit()


async def test_a_contact_is_found_by_name_address_or_number(
    authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    await _contacts(UUID(pod_id))

    async def found(q: str) -> list[str]:
        listed = await authenticated_client.get(
            f"/pods/{pod_id}/contacts", params={"q": q}
        )
        assert listed.status_code == 200, listed.text
        return [contact["label"] for contact in listed.json()["items"]]

    assert await found("REYES@client") == ["dana.reyes@client.example"]
    assert await found("omar") == ["Omar"]
    assert await found("555 010 44") == ["Omar"]
    assert await found("100%") == []
    everybody = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert sorted(contact["label"] for contact in everybody.json()["items"]) == [
        "Omar",
        "Web visitor",
        "dana.reyes@client.example",
    ]
