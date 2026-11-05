"""Which of a contact's handles a function may match them by: only proved ones."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.contacts.domain.entities import (
    Contact,
    ContactHandle,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.domain.verified import verified_handles

pytestmark = pytest.mark.unit

_EPOCH = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _contact(*handles: tuple[IdentityKind, str, IdentityStrength]) -> Contact:
    contact_id = uuid4()
    return Contact(
        id=contact_id,
        pod_id=uuid4(),
        display_name=None,
        created_at=_EPOCH,
        identities=tuple(
            ContactHandle(
                id=uuid4(),
                contact_id=contact_id,
                kind=kind,
                value=value,
                strength=strength,
                verified_at=_EPOCH,
            )
            for kind, value, strength in handles
        ),
    )


@pytest.mark.parametrize(
    "strength",
    [IdentityStrength.CHANNEL, IdentityStrength.HOST, IdentityStrength.CODE],
)
def test_a_handle_something_proved_is_listed(strength):
    contact = _contact((IdentityKind.EMAIL, "dana@example.com", strength))

    assert verified_handles(contact).emails == ("dana@example.com",)


@pytest.mark.parametrize(
    "strength", [IdentityStrength.MEMBER, IdentityStrength.SESSION]
)
def test_a_handle_nothing_proved_is_not(strength):
    """A member typing an address in says who they believe it is; a browser
    session says the same browser came back. Neither proves the address."""
    contact = _contact(
        (IdentityKind.EMAIL, "dana@example.com", strength),
        (IdentityKind.PHONE, "+14155550100", strength),
    )

    handles = verified_handles(contact)

    assert handles.emails == ()
    assert handles.phones == ()


def test_each_kind_goes_under_its_own_name_and_a_browser_under_none():
    contact = _contact(
        (IdentityKind.EMAIL, "dana@example.com", IdentityStrength.CODE),
        (IdentityKind.PHONE, "+14155550100", IdentityStrength.CHANNEL),
        (IdentityKind.TELEGRAM, "777000", IdentityStrength.CHANNEL),
        (IdentityKind.HOST, "host:acme:42", IdentityStrength.HOST),
        (IdentityKind.SLACK, "T1:U1", IdentityStrength.CHANNEL),
        (IdentityKind.TEAMS, "tenant:aad", IdentityStrength.CHANNEL),
        (IdentityKind.WEB, str(uuid4()), IdentityStrength.SESSION),
    )

    handles = verified_handles(contact)

    assert handles.contact_id == contact.id
    assert handles.model_dump(exclude={"contact_id"}) == {
        "emails": ("dana@example.com",),
        "phones": ("+14155550100",),
        "telegram_ids": ("777000",),
        "host_ids": ("host:acme:42",),
        "slack_ids": ("T1:U1",),
        "teams_ids": ("tenant:aad",),
    }


def test_a_contact_with_only_a_browser_has_an_id_and_nothing_else():
    contact = _contact((IdentityKind.WEB, str(uuid4()), IdentityStrength.SESSION))

    handles = verified_handles(contact)

    assert handles.contact_id == contact.id
    assert not any(handles.model_dump(exclude={"contact_id"}).values())


def test_a_kinds_handles_read_the_same_every_time():
    """Sorted, so two runs for one contact are told the same thing."""
    contact = _contact(
        (IdentityKind.EMAIL, "zoe@example.com", IdentityStrength.CHANNEL),
        (IdentityKind.EMAIL, "dana@example.com", IdentityStrength.CODE),
        (IdentityKind.EMAIL, "member-typed@example.com", IdentityStrength.MEMBER),
    )

    assert verified_handles(contact).emails == (
        "dana@example.com",
        "zoe@example.com",
    )
