"""Surfaces and notifications → email behaving like email.

Email is the surface with the most existing expectations. A reply that starts a
new thread, or arrives with a subject nobody recognises, is technically a reply
and practically a stranger's message — it lands outside the conversation the
person is reading and gets ignored.

Inbound is signed exactly as Resend signs it — the endpoint verifies a Svix
signature and refuses anything else, so a scenario has to produce a real one.

The *reply* is not here. A Resend surface authenticates with the deployment's
own API key and has no `api_base_url` override, so outbound mail can only go to
Resend itself — there is nothing to point at a server this suite runs, the way
the Telegram scenarios do. What a reply looks like is checked in the live lane,
against a real key; see `LIVE.md` and the note on PS-SURF-022.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from uuid import uuid4

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.stack import RESEND_INBOUND_DOMAIN, RESEND_WEBHOOK_SECRET
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Answer where the person already is"),
]

#: Mail from somebody Lemma knows. A stranger's mail is answered with how to
#: get access rather than opening a thread — a different promise (PS-SURF-012),
#: and one whose answer goes out through Resend, which is not observable here.


def _svix_headers(body: bytes) -> dict[str, str]:
    """Sign a payload the way Resend does, so the endpoint accepts it.

    The signed content is `id.timestamp.body` and the secret is base64 after its
    `whsec_` prefix. Getting this right is what makes the scenario about email
    routing rather than about a 401.
    """
    message_id = f"msg_{uuid4().hex[:16]}"
    timestamp = str(int(time.time()))
    secret = base64.b64decode(RESEND_WEBHOOK_SECRET.removeprefix("whsec_"))
    signature = base64.b64encode(
        hmac.new(
            secret, f"{message_id}.{timestamp}.{body.decode()}".encode(), hashlib.sha256
        ).digest()
    ).decode()
    return {
        "svix-id": message_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{signature}",
        "content-type": "application/json",
    }


@pytest.fixture
async def mailbox(world, run):
    """A pod reachable by email.

    Nothing captures what Lemma *sends*, and nothing used to either. A Resend
    surface authenticates with the deployment's own key and has no
    `api_base_url` to point elsewhere, so outbound mail can only go to Resend —
    the stand-in that used to be started here was never reached by anything.
    Inbound is real: the scenarios below sign a Svix payload themselves and post
    it to Lemma, which is what Resend does.
    """
    try:
        alice = await world.person("daniel")
        organization = alice.organization
        pod = await alice.creates_a_pod(named=run.name("pod"))
        agent = await alice.creates_an_agent(in_pod=pod)
        del organization
        # No connected account: an email surface authenticates with the
        # deployment's own Resend key and gets its own address under the
        # inbound domain. Sharing one key across pods is the design, not a
        # shortcut — see `PlatformCapabilities["RESEND"]`.
        surface = await alice.connects_a_surface(
            in_pod=pod, platform="RESEND", named="inbox", agent=agent["name"]
        )
        yield alice, pod, surface
    finally:
        pass


async def _deliver(
    alice, *, to: str, subject: str, text: str, message_id: str, sender: str | None = None
):
    import json as _json

    body = _json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": f"em_{uuid4().hex[:12]}",
                "from": sender or alice.email,
                "to": [to],
                "subject": subject,
                "text": text,
                "headers": {"message-id": message_id, "subject": subject},
            },
        }
    ).encode()
    return await alice.api.call(
        "POST",
        "/surfaces/webhooks/resend",
        content=body,
        headers=_svix_headers(body),
    )


@scenario("An email surface has its own address")
@proves("PS-SURF-022")
@covers("agent.surface.create", "agent.surface.get")
async def test_an_email_surface_has_an_address(mailbox):
    alice, pod, surface = mailbox
    del alice, pod

    address = _address_of(surface)

    assert address, (
        f"an email surface with no address cannot be written to: {surface}"
    )
    assert address.endswith(RESEND_INBOUND_DOMAIN), (
        f"the surface's address is not under this deployment's inbound domain, "
        f"so mail to it will never arrive: {address!r}"
    )


@scenario("Mail to a surface's address reaches the pod that owns it")
@proves("PS-SURF-022")
@covers("surface.webhook.handle_platform", "agent.conversation.list")
async def test_mail_reaches_the_pod_that_owns_the_address(mailbox):
    alice, pod, surface = mailbox
    address = _address_of(surface)
    incoming = f"<{uuid4().hex}@example.com>"

    delivered = await _deliver(
        alice,
        to=address,
        subject="A question about the numbers",
        text="Could you take a look?",
        message_id=incoming,
    )
    assert delivered.status_code < 400, (
        f"a correctly signed email was rejected: {delivered.status_code} "
        f"{delivered.text[:300]}"
    )

    # Routed to exactly the pod that owns the address, and readable there — an
    # email that arrives and starts nothing is the same as one that bounced,
    # except nobody is told.
    threads = await eventually(
        lambda: alice.conversations_in(pod),
        bool,
        describe="the email to open a conversation in the pod that owns the address",
        timeout=120.0,
    )
    # The thread is created before the message is persisted onto it, so reading
    # straight away finds an empty conversation and reports a working feature as
    # broken.
    said = await eventually(
        lambda: alice.messages_in(threads[0], in_pod=pod),
        lambda messages: any(
            "take a look" in str(message.get("text") or "") for message in messages
        ),
        describe="the email's words to reach the conversation it opened",
        timeout=60.0,
    )
    assert said, said


@scenario("Mail to an address nobody owns starts nothing")
@proves("PS-SURF-022")
@covers("surface.webhook.handle_platform")
async def test_mail_to_an_unknown_address_starts_nothing(mailbox):
    alice, pod, _surface = mailbox

    await _deliver(
        alice,
        to=f"nobody-{uuid4().hex[:8]}@{RESEND_INBOUND_DOMAIN}",
        subject="Hello?",
        text="Is anyone there?",
        message_id=f"<{uuid4().hex}@example.com>",
    )

    # There used to be an assertion here that no reply was sent. It could not
    # fail: nothing routed Lemma's outbound mail to the recorder it watched, so
    # the recorder was empty whatever happened. What is left is the half that
    # was always doing the work.
    assert not await alice.conversations_in(pod), (
        "mail to an address no surface owns started a conversation anyway"
    )


@scenario("An email that is not genuinely from the provider is refused")
@proves("PS-SURF-022", "PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_an_unsigned_email_is_refused(mailbox):
    alice, pod, surface = mailbox
    del pod
    address = _address_of(surface)

    refused = await alice.api.call(
        "POST",
        "/surfaces/webhooks/resend",
        json={"type": "email.received", "data": {"from": alice.email, "to": [address]}},
    )

    assert refused.status_code >= 400, (
        f"an unsigned email was accepted ({refused.status_code}); anyone who "
        f"knows the address could start an agent"
    )



def _address_of(surface) -> str:
    """The address a person writes to in order to reach this surface."""
    reach = surface.get("reach") or {}
    return str(reach.get("email") or surface.get("surface_identity_email") or "")
