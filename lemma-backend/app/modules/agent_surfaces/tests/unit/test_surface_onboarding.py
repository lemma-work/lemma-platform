"""The onboarding exchange, including every way it goes sideways.

A fake store rather than Redis: what is worth pinning here is the conversation
-- what gets said, what gets remembered between two messages, and what happens
when somebody mistypes -- not the round trip.
"""

from __future__ import annotations

import hashlib

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_onboarding_store import (
    PendingOnboarding,
)
from app.modules.agent_surfaces.services.surface_onboarding import (
    advance_onboarding,
    find_email,
    looks_like_code,
    mint_code,
)

pytestmark = pytest.mark.unit


class _Store:
    """The store's contract, in a dict."""

    def __init__(self) -> None:
        self.state: dict[str, PendingOnboarding] = {}

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()

    async def get(self, *, platform, sender_external_user_id):
        return self.state.get(f"{platform}:{sender_external_user_id}")

    async def put(self, *, platform, sender_external_user_id, pending):
        self.state[f"{platform}:{sender_external_user_id}"] = pending

    async def clear(self, *, platform, sender_external_user_id):
        self.state.pop(f"{platform}:{sender_external_user_id}", None)


def _message(text: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="14155550001",
        sender_external_user_id="14155550001",
        message_text=text,
        is_dm=True,
    )


def _sends_ok():
    sent: list[dict] = []

    async def send(*, to_email, code, surface_label):
        sent.append({"to": to_email, "code": code, "surface": surface_label})
        return True

    return send, sent


async def test_the_whole_exchange_from_stranger_to_proven() -> None:
    store = _Store()
    send, sent = _sends_ok()

    opening = await advance_onboarding(
        store=store, event=_message("can you check this invoice?"), send_code_email=send
    )
    assert opening is not None and "email" in opening.message.lower()
    assert opening.proven_email is None

    asked = await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )
    assert asked is not None
    assert "ada@acme.com" in asked.message
    assert sent[0]["to"] == "ada@acme.com"
    # The mail names the surface, so an innocent recipient can place it.
    assert sent[0]["surface"] == "WhatsApp"

    done = await advance_onboarding(
        store=store, event=_message(sent[0]["code"]), send_code_email=send
    )
    assert done is not None
    assert done.proven_email == "ada@acme.com"
    # Nothing left behind to replay.
    assert store.state == {}


async def test_a_code_is_read_out_of_whatever_it_arrives_in() -> None:
    """People reply "my code is H7K2QM" and mean the code."""
    store = _Store()
    send, sent = _sends_ok()
    await advance_onboarding(store=store, event=_message("hello"), send_code_email=send)
    await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )

    done = await advance_onboarding(
        store=store,
        event=_message(f"my code is {sent[0]['code'].lower()}!"),
        send_code_email=send,
    )
    assert done is not None and done.proven_email == "ada@acme.com"


async def test_three_wrong_codes_burn_the_attempt() -> None:
    store = _Store()
    send, _sent = _sends_ok()
    await advance_onboarding(store=store, event=_message("hi"), send_code_email=send)
    await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )

    for _ in range(2):
        turn = await advance_onboarding(
            store=store, event=_message("AAAAAA"), send_code_email=send
        )
        assert turn is not None and turn.proven_email is None
        assert "didn't match" in turn.message

    burned = await advance_onboarding(
        store=store, event=_message("AAAAAA"), send_code_email=send
    )
    assert burned is not None and "start over" in burned.message
    assert store.state == {}


async def test_chatter_while_waiting_for_a_code_does_not_cost_a_try() -> None:
    """ "please" is a six-letter word, not a wrong guess.

    Spending one of somebody's three tries because they were polite would be a
    strange way to run an identity check.
    """
    store = _Store()
    send, _sent = _sends_ok()
    await advance_onboarding(store=store, event=_message("hi"), send_code_email=send)
    await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )

    for text in ("please", "sorry what", "hang on"):
        turn = await advance_onboarding(
            store=store, event=_message(text), send_code_email=send
        )
        assert turn is not None and "Send me the code" in turn.message

    pending = await store.get(
        platform="WHATSAPP", sender_external_user_id="14155550001"
    )
    assert pending is not None and pending.attempts == 0


async def test_an_exchange_going_nowhere_eventually_goes_quiet() -> None:
    """Onboarding is exempt from the stranger window, so it bounds itself.

    Every reply is an outbound message on a number every pod shares, and
    somebody typing at it forever must not be able to make us answer forever.
    A real signup spends three or four turns; this spends them on nothing.
    """
    store = _Store()
    send, sent = _sends_ok()

    replies = 0
    for _ in range(20):
        turn = await advance_onboarding(
            store=store, event=_message("still not an email"), send_code_email=send
        )
        if turn is None:
            break
        replies += 1

    assert replies < 20, "the exchange must stop answering at some point"
    assert sent == [], "and never mailed anybody, since no address was ever given"

    # Silence, not a refusal message -- there is nothing useful left to say.
    assert (
        await advance_onboarding(
            store=store, event=_message("hello?"), send_code_email=send
        )
        is None
    )


async def test_a_real_signup_fits_inside_the_budget() -> None:
    """The bound has to be generous enough for somebody doing it properly."""
    store = _Store()
    send, sent = _sends_ok()

    await advance_onboarding(store=store, event=_message("hi"), send_code_email=send)
    # A typo, a correction, then the code: an ordinary slightly-fumbled signup.
    await advance_onboarding(
        store=store, event=_message("ada@acme"), send_code_email=send
    )
    await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )
    await advance_onboarding(
        store=store, event=_message("hang on"), send_code_email=send
    )
    done = await advance_onboarding(
        store=store, event=_message(sent[0]["code"]), send_code_email=send
    )
    assert done is not None and done.proven_email == "ada@acme.com"


async def test_a_second_address_is_taken_as_a_correction_not_a_wrong_code() -> None:
    """Mistyping an address should not cost somebody their three tries."""
    store = _Store()
    send, _sent = _sends_ok()
    await advance_onboarding(store=store, event=_message("hi"), send_code_email=send)
    await advance_onboarding(
        store=store, event=_message("ada@acme.con"), send_code_email=send
    )

    correction = await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=send
    )
    assert correction is not None
    assert "ada@acme.com" in correction.message
    pending = await store.get(
        platform="WHATSAPP", sender_external_user_id="14155550001"
    )
    assert pending is not None and pending.step == "awaiting_email"


async def test_a_code_that_could_not_be_sent_is_never_announced() -> None:
    """Nobody should sit watching an inbox nothing is coming to."""
    store = _Store()

    async def fails(*, to_email, code, surface_label):
        return False

    await advance_onboarding(store=store, event=_message("hi"), send_code_email=fails)
    turn = await advance_onboarding(
        store=store, event=_message("ada@acme.com"), send_code_email=fails
    )
    assert turn is not None
    assert "couldn't send" in turn.message
    # Still waiting for an address, not for a code that does not exist.
    pending = await store.get(
        platform="WHATSAPP", sender_external_user_id="14155550001"
    )
    assert pending is not None and pending.step == "awaiting_email"


async def test_something_that_is_not_an_address_asks_again() -> None:
    store = _Store()
    send, sent = _sends_ok()
    await advance_onboarding(store=store, event=_message("hi"), send_code_email=send)

    turn = await advance_onboarding(
        store=store, event=_message("who is this"), send_code_email=send
    )
    assert turn is not None
    assert "doesn't look like an email" in turn.message
    assert sent == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ada@acme.com", "ada@acme.com"),
        ("  ADA@Acme.com.", "ada@acme.com"),
        ("mail me at ada@acme.com please", "ada@acme.com"),
        ("no address here", None),
        ("almost@nowhere", None),
    ],
)
def test_an_address_is_found_however_it_is_wrapped(text, expected) -> None:
    assert find_email(text) == expected


def test_a_minted_code_avoids_the_characters_people_misread() -> None:
    for _ in range(50):
        code = mint_code()
        assert len(code) == 6
        assert not set(code) & set("OI01")
        assert looks_like_code(code) == code
