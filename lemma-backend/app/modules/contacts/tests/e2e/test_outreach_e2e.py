"""The outreach rules against real stores: suppressions in Postgres, the day's
count in Redis, and the unsubscribe controller a mail client posts to.

The rules' order is pinned without stores in ``unit/test_outreach_rules``; the
paths that ask them -- a member's line, an agent's answer, STOP arriving --
in ``agent_surfaces/tests/e2e`` (``test_channel_deliveries_e2e``,
``test_contact_door_e2e``, ``test_email_contacts_e2e``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.redis.client import get_redis
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    find_contact,
    open_contact,
    prove_handle,
)
from app.modules.contacts.contracts.outreach import (
    Address,
    OutreachKind,
    RefusalCode,
    SendWindow,
    SuppressionReason,
    Verdict,
    count_against_today,
    heard_from,
    may_send,
    opt_out,
    unsubscribe_link,
)
from app.modules.contacts.services.unsubscribe import unsubscribe_token

pytestmark = pytest.mark.e2e

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
PHONE = "+1 (415) 555-0100"
WA_ID = "14155550100"
EMAIL = "dana@client.example"


async def _contact(pod_id: UUID, kind: IdentityKind, value: str) -> ContactRef:
    async with get_uow_factory()() as uow:
        contact = await open_contact(
            uow,
            pod_id=pod_id,
            kind=kind,
            value=value,
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        await uow.commit()
    return contact


async def _may_send(
    pod_id: UUID,
    contact_id: UUID,
    to: Address,
    *,
    kind: OutreachKind = OutreachKind.MEMBER_LINE,
    window: SendWindow | None = None,
) -> Verdict:
    async with get_uow_factory()() as uow:
        return await may_send(
            uow,
            pod_id=pod_id,
            contact_id=contact_id,
            to=to,
            kind=kind,
            at=NOW,
            window=window,
        )


async def _stop(pod_id: UUID, kind: IdentityKind, value: str) -> None:
    async with get_uow_factory()() as uow:
        await opt_out(
            uow, pod_id=pod_id, kind=kind, value=value, reason=SuppressionReason.STOP
        )
        await uow.commit()


# ------------------------------------------------------------ one spelling


async def test_a_number_however_spelled_is_one_contact(test_pod):
    """A member typing it in, and WhatsApp naming the sender, meet one person."""
    pod_id = UUID(test_pod["id"])
    typed = await _contact(pod_id, IdentityKind.PHONE, PHONE)

    async with get_uow_factory()() as uow:
        found = await find_contact(
            uow, pod_id=pod_id, kind=IdentityKind.PHONE, value=WA_ID
        )
    again = await _contact(pod_id, IdentityKind.PHONE, f"+{WA_ID}")

    assert found is not None and found.id == typed.id
    assert again.id == typed.id


async def test_proving_a_number_spelled_another_way_merges_into_its_holder(test_pod):
    pod_id = UUID(test_pod["id"])
    holder = await _contact(pod_id, IdentityKind.PHONE, WA_ID)
    other = await _contact(pod_id, IdentityKind.EMAIL, EMAIL)

    async with get_uow_factory()() as uow:
        proven = await prove_handle(
            uow,
            pod_id=pod_id,
            claimant_id=other.id,
            kind=IdentityKind.PHONE,
            value=PHONE,
            strength=IdentityStrength.CODE,
        )
        await uow.commit()

    assert proven.id == holder.id


async def test_an_opt_out_spelled_one_way_holds_for_every_spelling(test_pod):
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.PHONE, WA_ID)
    await _stop(pod_id, IdentityKind.PHONE, PHONE)

    verdict = await _may_send(
        pod_id, contact.id, Address(kind=IdentityKind.PHONE, value=WA_ID)
    )

    assert verdict.code is RefusalCode.SUPPRESSED


# ------------------------------------------------------------ opt-out first


@pytest.mark.parametrize("kind", list(OutreachKind))
async def test_an_opt_out_refuses_every_kind_of_line(test_pod, kind):
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.EMAIL, EMAIL)
    await _stop(pod_id, IdentityKind.EMAIL, EMAIL)

    verdict = await _may_send(
        pod_id,
        contact.id,
        Address(kind=IdentityKind.EMAIL, value=EMAIL),
        kind=kind,
        # A shut window too: the opt-out is the answer, not the window.
        window=SendWindow(closes_at=None, explanation="shut"),
    )

    assert verdict.code is RefusalCode.SUPPRESSED
    assert not verdict.retryable


async def test_an_opt_out_survives_the_contact_being_forgotten(
    authenticated_client, test_pod
):
    """Forgotten, then met again from the same number: still opted out."""
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.PHONE, WA_ID)
    await _stop(pod_id, IdentityKind.PHONE, WA_ID)

    forgot = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{contact.id}")
    assert forgot.status_code == 204, forgot.text

    gone = await _may_send(
        pod_id, contact.id, Address(kind=IdentityKind.PHONE, value=WA_ID)
    )
    assert gone.code is RefusalCode.SUPPRESSED
    met_again = await _contact(pod_id, IdentityKind.PHONE, PHONE)
    assert met_again.id != contact.id
    verdict = await _may_send(
        pod_id, met_again.id, Address(kind=IdentityKind.PHONE, value=WA_ID)
    )
    assert verdict.code is RefusalCode.SUPPRESSED


async def test_an_opt_out_is_one_pods(test_pod, authenticated_client, fixed_test_org):
    pod_id = UUID(test_pod["id"])
    other = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Other {uuid4()}",
            "slug": f"other-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert other.status_code == 201, other.text
    other_pod = UUID(other.json()["id"])
    theirs = await _contact(other_pod, IdentityKind.EMAIL, EMAIL)
    await _stop(pod_id, IdentityKind.EMAIL, EMAIL)

    verdict = await _may_send(
        other_pod, theirs.id, Address(kind=IdentityKind.EMAIL, value=EMAIL)
    )

    assert verdict.allowed


async def test_writing_again_opts_back_in_only_when_they_typed_it(test_pod):
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.EMAIL, EMAIL)
    to = Address(kind=IdentityKind.EMAIL, value=EMAIL)
    await _stop(pod_id, IdentityKind.EMAIL, EMAIL)

    async def heard(typed: bool) -> bool:
        async with get_uow_factory()() as uow:
            lifted = await heard_from(
                uow,
                pod_id=pod_id,
                kind=IdentityKind.EMAIL,
                value="Dana@Client.example",
                typed=typed,
            )
            await uow.commit()
        return lifted

    assert await heard(False) is False
    assert (await _may_send(pod_id, contact.id, to)).code is RefusalCode.SUPPRESSED
    assert await heard(True) is True
    assert (await _may_send(pod_id, contact.id, to)).allowed


# ------------------------------------------------------------ email


async def test_an_email_carries_the_footer_and_the_token_for_its_address(test_pod):
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.EMAIL, EMAIL)

    verdict = await _may_send(
        pod_id,
        contact.id,
        Address(kind=IdentityKind.EMAIL, value="DANA@client.example"),
    )

    assert verdict.allowed
    assert verdict.unsubscribe_token == unsubscribe_token(
        pod_id, IdentityKind.EMAIL, EMAIL
    )
    assert unsubscribe_link(verdict.unsubscribe_token) in verdict.footer


async def test_a_mail_clients_one_click_post_opts_the_address_out(
    async_client, test_pod
):
    """RFC 8058: a POST to the header's link, the token in its URL and
    ``List-Unsubscribe=One-Click`` as its body -- no page, no session."""
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.EMAIL, EMAIL)
    token = unsubscribe_token(pod_id, IdentityKind.EMAIL, EMAIL)

    forged = await async_client.post(
        "/public/contacts/unsubscribe",
        params={"token": token[:-2] + "xx"},
        data={"List-Unsubscribe": "One-Click"},
    )
    assert "This link doesn" in forged.text
    assert (
        await _may_send(
            pod_id, contact.id, Address(kind=IdentityKind.EMAIL, value=EMAIL)
        )
    ).allowed

    clicked = await async_client.post(
        "/public/contacts/unsubscribe",
        params={"token": token},
        data={"List-Unsubscribe": "One-Click"},
    )

    assert clicked.status_code == 200 and "unsubscribed" in clicked.text
    verdict = await _may_send(
        pod_id, contact.id, Address(kind=IdentityKind.EMAIL, value=EMAIL)
    )
    assert verdict.code is RefusalCode.SUPPRESSED


async def test_a_link_signed_for_a_deleted_pod_says_it_no_longer_works(
    async_client,
):
    """Signed, so not a forgery -- but its pod is gone, and there is nothing
    to keep the opt-out against. It used to fail on the pod's foreign key."""
    token = unsubscribe_token(uuid4(), IdentityKind.EMAIL, EMAIL)

    clicked = await async_client.post(
        "/public/contacts/unsubscribe",
        params={"token": token},
        data={"List-Unsubscribe": "One-Click"},
    )

    assert clicked.status_code == 404, clicked.text
    assert "This link no longer works" in clicked.text


# ------------------------------------------------------------ windows and caps


async def test_a_platforms_window_refuses_with_its_own_words(test_pod):
    pod_id = UUID(test_pod["id"])
    contact = await _contact(pod_id, IdentityKind.PHONE, WA_ID)
    to = Address(kind=IdentityKind.PHONE, value=WA_ID)

    shut = await _may_send(
        pod_id,
        contact.id,
        to,
        window=SendWindow(closes_at=NOW - timedelta(minutes=1), explanation="24h"),
    )
    open_ = await _may_send(
        pod_id,
        contact.id,
        to,
        window=SendWindow(closes_at=NOW + timedelta(minutes=1), explanation="24h"),
    )

    assert (shut.code, shut.reason) == (RefusalCode.OUTSIDE_WINDOW, "24h")
    assert open_.allowed


async def test_a_contacts_day_counts_each_line_once(test_pod):
    contact_id = uuid4()
    first, second = uuid4(), uuid4()

    assert (
        await count_against_today(contact_id=contact_id, line_id=first, limit=1)
    ).allowed
    # A retry of the same line is the same line.
    assert (
        await count_against_today(contact_id=contact_id, line_id=first, limit=1)
    ).allowed
    full = await count_against_today(contact_id=contact_id, line_id=second, limit=1)
    assert (full.code, full.retryable) == (RefusalCode.RATE_LIMITED, False)
    # And the day expires.
    ttl = await get_redis().ttl(f"contact:lines:{contact_id}")
    assert 0 < ttl <= 86_400


async def test_without_redis_a_line_waits_rather_than_going_uncounted(test_pod):
    class _Away:
        async def eval(self, *args, **kwargs):
            from redis.exceptions import ConnectionError

            raise ConnectionError("connection refused")

    verdict = await count_against_today(
        contact_id=uuid4(),
        line_id=uuid4(),
        redis=_Away(),  # type: ignore[arg-type]
    )

    assert (verdict.code, verdict.retryable) == (RefusalCode.UNAVAILABLE, True)
