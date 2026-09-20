"""Signing in to sites → what is kept, who can see it, and taking it away.

Black-box, over the shipped API. These are the promises a person could check
themselves: my saved logins are mine, nobody else can see or remove them, and
what the platform holds is never handed back — not even to me.

The sign-in flow itself needs a real browser in a real sandbox, so it lives in
the workspace e2e suite rather than here. What this proves is everything a
person can verify without one.
"""

from __future__ import annotations

from uuid import uuid4

from harness import capability, covers, journey, proves, scenario
from harness.drivers.api import items_of

pytestmark = [
    journey("Signing in to sites"),
    capability("Keep a way back in"),
]


@scenario("A person with no saved logins sees an empty shelf, not an error")
@proves("PS-BROWSER-022")
@covers("web_login.list")
async def test_nothing_saved_is_an_empty_list(world) -> None:
    alice = await world.person("priya")
    assert items_of(await alice.api.get("/web-logins")) == []


@scenario("Saved logins are per person, and nobody else can list them")
@proves("PS-BROWSER-020")
@covers("web_login.list")
async def test_one_persons_logins_are_not_anothers(world) -> None:
    """The list is keyed by the session, not by anything the caller passes.

    Which is why there is no id to tamper with: the strongest version of "one
    person may not use another's" is one where there is nothing to ask for.
    """
    alice = await world.person("priya")
    bob = await world.person("daniel")

    for person in (alice, bob):
        assert items_of(await person.api.get("/web-logins")) == []

    # And the route takes no user parameter at all -- there is no way to ask
    # for somebody else's, correctly or otherwise. The body carries the
    # listing and whether the computer was asleep, and nothing else: no
    # owner, no id, nothing a caller could change to mean a different person.
    # The sandbox this reads is resolved from the session, so the isolation is
    # the same one that makes a person's files theirs.
    mine = await alice.api.get("/web-logins")
    assert set(mine) <= {"items", "sleeping"}, mine


@scenario("Signing out needs a running computer, and says so when there is none")
@proves("PS-BROWSER-022")
@covers("web_login.delete")
async def test_signing_out_without_a_running_browser_is_refused(world) -> None:
    """Refusing beats reporting a success that did not happen.

    This is the whole difference from the delete it replaces. That one removed
    an encrypted copy Lemma kept and left the browser signed in, so it could
    always claim to have worked. This changes the browser, and a browser that
    is not running has not been changed.
    """
    alice = await world.person("priya")
    await alice.api.expect(
        "DELETE",
        "/web-logins",
        params={"origin": "https://never-saved.example.com"},
        status=409,
        what="signing out while the computer is asleep",
    )


@scenario("A sign-in request opens only for the person it was made for")
@proves("PS-BROWSER-010")
@covers("web_login.sign_in.pending")
async def test_somebody_elses_request_is_not_found(world) -> None:
    """The link is safe to send because the id in it grants nothing.

    A stranger, and the owner of a link that was forwarded to them, get the
    same answer as somebody who invented the id: not found. Anything else would
    tell a holder of a guessed id that they had guessed right.

    What this half proves is the invented id. Raising a real pause needs a run
    that stops on one, so the branch that turns *somebody else's* conversation
    into the same 404 is proved against the service in
    `test_sign_in_service.py::test_a_stranger_cannot_read_what_somebody_is_being_asked_to_sign_in_to`.
    """
    alice = await world.person("priya")
    await alice.api.expect(
        "GET",
        f"/web-logins/sign-ins/{uuid4()}/call_invented",
        status=404,
        what="opening a sign-in that is not yours",
    )


@scenario("Signing in cannot be finished by somebody it was not asked of")
@proves("PS-BROWSER-012")
@covers("web_login.sign_in.answer")
async def test_finishing_somebody_elses_request_is_refused(world) -> None:
    """As above, the owned-by-somebody-else branch is proved against the
    service, in `test_a_stranger_cannot_answer_somebody_elses_sign_in`."""
    alice = await world.person("priya")
    await alice.api.expect(
        "POST",
        f"/web-logins/sign-ins/{uuid4()}/call_invented/answer",
        json={"signed_in": True, "force": False},
        status=404,
        what="answering a sign-in that is not yours",
    )


@scenario("Watching a browser does not start a computer that is asleep")
@proves("PS-BROWSER-030")
@covers("workspace.browser.status")
async def test_asking_whether_a_browser_can_be_watched_starts_nothing(world) -> None:
    """A panel rendering must not be what provisions a sandbox.

    The honest answer for somebody who has never run anything is "asleep", and
    getting it must not cost them a container.
    """
    alice = await world.person("priya")
    status = await alice.api.get("/workspace/browser/status")
    assert status["state"] in {"asleep", "stopped", "unavailable", "unsupported"}
