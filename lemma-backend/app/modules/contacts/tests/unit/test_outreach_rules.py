"""What the pod may send a contact: the rules, in their order, over facts.

``domain.outreach.decide`` is pinned directly. How a line to a contact's place
is held to it -- the delivery row, the suppressions table, the day's count in
Redis -- is followed against real stores in
``contacts/tests/e2e/test_outreach_e2e.py`` and
``agent_surfaces/tests/e2e/test_channel_deliveries_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from uuid import uuid4

import pytest

from app.modules.contacts.contracts import (
    ContactHandle,
    IdentityKind,
    IdentityStrength,
)
from app.modules.contacts.contracts.outreach import (
    Address,
    RefusalCode,
    SendWindow,
    read_unsubscribe_token,
    unsubscribe_headers,
)
from app.modules.contacts.domain.handles import handle_hash
from app.modules.contacts.domain.outreach import decide
from app.modules.contacts.services.unsubscribe import (
    unsubscribe_footer,
    unsubscribe_token,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def _handle(kind: IdentityKind, value: str, *, wrote=None) -> ContactHandle:
    return ContactHandle(
        id=uuid4(),
        contact_id=uuid4(),
        kind=kind,
        value=value,
        strength=IdentityStrength.CHANNEL,
        verified_at=NOW,
        last_inbound_at=wrote,
    )


def _window(closes_at) -> SendWindow:
    return SendWindow(closes_at=closes_at, explanation="shut")


# ------------------------------------------------------------------ order


def test_an_opt_out_wins_over_every_other_rule():
    """Suppressed and outside the window and no handle: STOP is the answer."""
    verdict = decide(
        to=Address(kind=IdentityKind.PHONE, value="+447700900001"),
        handles=[],
        suppressed={"+447700900001"},
        at=NOW,
        window=_window(None),
    )

    assert verdict.code is RefusalCode.SUPPRESSED
    assert not verdict.retryable


def test_an_address_nobody_holds_any_more_is_still_opted_out():
    """The contact was forgotten; the place's number said STOP."""
    verdict = decide(
        to=Address(kind=IdentityKind.PHONE, value="447700900001"),
        handles=[],
        suppressed={"+447700900001"},
        at=NOW,
        window=None,
    )

    assert verdict.code is RefusalCode.SUPPRESSED


def test_a_contact_with_no_handle_there_is_refused():
    verdict = decide(
        to=Address(kind=IdentityKind.TELEGRAM),
        handles=[_handle(IdentityKind.EMAIL, "dana@example.com")],
        suppressed=set(),
        at=NOW,
        window=None,
    )

    assert verdict.code is RefusalCode.NO_HANDLE


def test_one_opted_out_address_silences_only_that_address():
    work = _handle(IdentityKind.EMAIL, "dana@old.example", wrote=NOW)
    personal = _handle(IdentityKind.EMAIL, "dana@home.example", wrote=NOW)

    def to(value):
        return decide(
            to=Address(kind=IdentityKind.EMAIL, value=value),
            handles=[work, personal],
            suppressed={"dana@old.example"},
            at=NOW,
            window=None,
        )

    assert to(None).handle == personal
    assert to("Dana@Home.example").handle == personal
    assert to("dana@old.example").code is RefusalCode.SUPPRESSED


def test_every_handle_of_the_kind_opted_out_is_suppressed_not_missing():
    verdict = decide(
        to=Address(kind=IdentityKind.EMAIL),
        handles=[_handle(IdentityKind.EMAIL, "dana@old.example")],
        suppressed={"dana@old.example"},
        at=NOW,
        window=None,
    )

    assert verdict.code is RefusalCode.SUPPRESSED


def test_a_shut_window_refuses_with_the_platforms_words():
    handle = _handle(IdentityKind.PHONE, "+447700900001", wrote=NOW)

    def at(window):
        return decide(
            to=Address(kind=IdentityKind.PHONE, value="+447700900001"),
            handles=[handle],
            suppressed=set(),
            at=NOW,
            window=window,
        )

    assert at(_window(NOW + timedelta(minutes=1))).allowed
    shut = at(_window(NOW - timedelta(minutes=1)))
    assert shut.code is RefusalCode.OUTSIDE_WINDOW
    assert shut.reason == "shut"
    assert at(_window(None)).code is RefusalCode.OUTSIDE_WINDOW
    assert at(None).allowed


def test_the_latest_heard_from_handle_decides_when_the_place_names_none():
    stale = _handle(IdentityKind.PHONE, "+447700900001", wrote=NOW - timedelta(days=3))
    fresh = _handle(IdentityKind.PHONE, "+447700900002", wrote=NOW)

    verdict = decide(
        to=Address(kind=IdentityKind.PHONE),
        handles=[stale, fresh],
        suppressed=set(),
        at=NOW,
        window=None,
    )

    assert verdict.handle == fresh


def test_a_place_names_its_number_however_it_is_spelled():
    old = _handle(IdentityKind.PHONE, "+447700900001", wrote=NOW - timedelta(days=1))
    new = _handle(IdentityKind.PHONE, "+447700900002", wrote=NOW)

    verdict = decide(
        to=Address(kind=IdentityKind.PHONE, value="+44 7700 900001"),
        handles=[old, new],
        suppressed=set(),
        at=NOW,
        window=None,
    )

    assert verdict.handle == old


# ------------------------------------------------------------------ unsubscribe


def test_an_unsubscribe_token_names_one_handle_in_one_pod_and_cannot_be_altered():
    pod_id = uuid4()
    token = unsubscribe_token(pod_id, IdentityKind.EMAIL, "Dana@Example.com")

    target = read_unsubscribe_token(token)
    assert target is not None
    assert target.pod_id == pod_id
    assert target.kind is IdentityKind.EMAIL
    assert target.value_hash == handle_hash(IdentityKind.EMAIL, "dana@example.com")

    payload, _, signature = token.partition(".")
    other_pod = payload.replace(str(pod_id), str(uuid4()))
    assert read_unsubscribe_token(f"{other_pod}.{signature}") is None
    assert read_unsubscribe_token("junk") is None
    assert len(token) <= 256


def test_the_footer_and_the_headers_carry_the_same_link():
    token = unsubscribe_token(uuid4(), IdentityKind.EMAIL, "dana@example.com")

    footer = unsubscribe_footer(token)
    headers = unsubscribe_headers(token, mailto="bot@mail.example")

    assert "To stop these emails:" in footer
    link = footer.rsplit(": ", 1)[1]
    assert read_unsubscribe_token(unquote(link.rsplit("token=", 1)[1])) is not None
    assert headers["List-Unsubscribe"] == (
        f"<{link}>, <mailto:bot@mail.example?subject=unsubscribe>"
    )
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert unsubscribe_headers(token)["List-Unsubscribe"] == f"<{link}>"
