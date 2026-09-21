"""Who may tap the button that approves an action.

`interaction_sender_matches` is the whole authorization story for a native
interaction: it runs between the replay-dedup claim and
`resolve_user_approval_internal`, so what it allows is what executes. It had no
test at all.

It used to return True whenever *either* id was empty, and both are empty in
ordinary traffic -- a thread opened by a notification whose channel carried no
address, a Slack payload with no `event.user`, a Teams one with neither
`aadObjectId` nor `from.id`. So "we cannot tell who tapped" meant "anyone may",
on the control in front of a destructive action's Approve.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.interaction_helpers import (
    _within_authorized_scope,
    interaction_sender_matches,
)

pytestmark = pytest.mark.unit


def _pair(link_id, sender_id):
    return (
        SimpleNamespace(external_user_id=link_id),
        SimpleNamespace(external_user_id=sender_id),
    )


def test_the_person_the_question_was_shown_to_may_answer_it() -> None:
    assert interaction_sender_matches(*_pair("U-alice", "U-alice"))


def test_somebody_else_in_the_channel_may_not() -> None:
    assert not interaction_sender_matches(*_pair("U-alice", "U-mallory"))


def test_a_link_that_names_nobody_does_not_admit_everybody() -> None:
    """The fail-open half. A notification-opened thread looks exactly like this."""
    assert not interaction_sender_matches(*_pair(None, "U-mallory"))
    assert not interaction_sender_matches(*_pair("", "U-mallory"))


def test_a_tap_that_names_nobody_is_not_the_owner_either() -> None:
    """Every interaction parser can yield None here, not just the exotic ones."""
    assert not interaction_sender_matches(*_pair("U-alice", None))
    assert not interaction_sender_matches(*_pair("U-alice", ""))


def test_neither_side_naming_anybody_is_still_a_refusal() -> None:
    assert not interaction_sender_matches(*_pair(None, None))


@pytest.mark.parametrize(
    "link_id, sender_id",
    [("U-alice", "u-alice"), (" U-alice ", "U-alice"), ("U-ALICE", "u-alice")],
)
def test_one_person_spelled_two_ways_is_one_person(link_id, sender_id) -> None:
    """Neither side was folded before, so case alone read as a different human.

    Harmless while the check was fail-open; with a strict match it would lock
    the owner out of their own approval.
    """
    assert interaction_sender_matches(*_pair(link_id, sender_id))


def test_a_missing_attribute_is_treated_as_naming_nobody() -> None:
    """Refusing beats raising: an AttributeError here reaches the person as
    "I couldn't complete that action" from the handler's own catch, with the
    approval still pending and nothing said about why."""
    assert not interaction_sender_matches(SimpleNamespace(), SimpleNamespace())


def test_the_refusal_is_visible_to_an_operator() -> None:
    """It now fires on ordinary traffic, so it cannot stay at debug.

    `LOG_LEVEL=INFO` drops debug before formatting, which is how nobody would
    learn that a shape of thread had lost its buttons.
    """
    from app.core.log.event_catalog import EVENT_CATALOG

    spec = EVENT_CATALOG[
        "agent_surfaces.ingress_service.interaction_submitter_refused.degraded"
    ]
    assert spec.level == "warning"


def _link(surface_id):
    return SimpleNamespace(surface_id=surface_id, conversation_id=uuid4())


def test_a_conversation_on_a_surface_this_request_proved_is_reachable() -> None:
    """The ordinary case, asserted beside the refusals it must not become.

    A check that refuses everything passes every test written about refusals.
    """
    mine = uuid4()
    assert _within_authorized_scope(_link(mine), [mine, uuid4()])


def test_a_conversation_on_somebody_elses_surface_is_not() -> None:
    """The cross-tenant hole this exists to close.

    The button carries an unsigned `conversation_id|tool_call_id`, and a Slack
    or Teams tenant holds its own signing secret -- so anyone running their own
    app can mint a correctly-signed payload naming any conversation id and any
    `user.id`. Before this, the id alone decided whose conversation was
    resolved: the victim's surface was loaded, their credentials returned, and
    an Approve resolved their paused tool call as them, in their pod.

    `interaction_sender_matches` was the only thing left standing, and it
    compares two values that same attacker supplies. The receiver list is
    derived from the workspace the signature actually proved, which is the one
    boundary the payload cannot cross.
    """
    assert not _within_authorized_scope(_link(uuid4()), [uuid4(), uuid4()])


def test_no_receiver_list_means_our_own_secret_verified_it() -> None:
    """`None` is not "skip the check" -- it is "there is no subset to check".

    It survives only where the payload was verified with a secret of ours: the
    shared Telegram bot, the shared WhatsApp number. Those serve every pod, so
    the delivering receiver names no surfaces and the sender match is the
    control. Treating `None` as a refusal would silently kill every shared-bot
    button instead.
    """
    assert _within_authorized_scope(_link(uuid4()), None)


def test_an_empty_receiver_list_refuses_rather_than_admits() -> None:
    """A receiver serving nothing proved nothing.

    The dangerous reading of an empty list is "no restriction". It is the
    opposite, and it matches how `routing_surfaces` reads an empty
    `surface_ids` -- `IN ()` matches nothing.
    """
    assert not _within_authorized_scope(_link(uuid4()), [])
