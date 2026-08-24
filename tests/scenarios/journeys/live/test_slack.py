"""Live → a real Slack workspace, through an account somebody connected.

Slack is the one platform of the four where a full round trip needs no person
at the moment it runs: a bot cannot cold-DM, but it can post in a channel it
belongs to. So this writes, reads its own writing back, and takes it away
again — against the real workspace, with no stand-in anywhere.

The account comes from OAuth, because `connector_service` refuses credential
injection for an OAuth2 connector and is right to. `needs(SLACK)` skips with
instructions where nobody has consented, and the run says so under "waiting on
a person" rather than quietly proving less.

Payload shapes differ per operation and are not guesswork: each operation
publishes an input schema, and `chat_post_message` takes a `body` object while
`conversations_list` and `conversations_history` take theirs flat. Reading the schema is what
an agent does too.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.consent import SLACK
from harness.credentials import needs
from harness.run import current
from harness.tenant import CONNECTOR_HOLDER, standing_auth_config_name

pytestmark = [
    journey("Connectors and accounts"),
    capability("Use a connector"),
    pytest.mark.live,
]


@pytest.fixture
async def slack(world):
    """The holder, and the auth config their Slack account hangs from."""
    holder = await world.person(CONNECTOR_HOLDER)
    needs(SLACK)
    return holder, holder.organization["id"], standing_auth_config_name("slack")


async def _run(holder, organization, config, operation, payload):
    """One connector operation, as an agent or a person would run it."""
    answered = await holder.api.post(
        f"/organizations/{organization}/connectors/{config}/operations/{operation}/execute",
        what=f"running {operation}",
        json={"payload": payload},
    )
    return answered.get("result") or {}


@scenario("Connecting Slack tells Lemma which workspace it is")
@proves("PS-CONN-011", "PS-CONN-020")
@covers("connector.operation.execute")
async def test_connecting_slack_identifies_the_workspace(slack):
    holder, organization, config = slack

    identity = await _run(holder, organization, config, "auth_test", {})

    assert identity.get("ok") is True, f"Slack refused auth.test: {identity}"
    assert identity.get("team"), (
        f"Slack answered without naming the workspace: {identity}. That name is "
        f"what tells somebody which workspace they connected."
    )


@scenario("A message posted through Lemma really is in the channel")
@proves("PS-CONN-030")
@covers("connector.operation.execute")
async def test_a_message_is_posted_and_taken_back(slack):
    holder, organization, config = slack

    channels = await _run(
        holder, organization, config, "conversations_list", {"limit": 200}
    )
    joined = [c for c in channels.get("channels") or [] if c.get("is_member")]
    if not joined:
        pytest.skip(
            "the connected Slack app is not in any channel — invite it to one, "
            "since a bot can only post where it belongs"
        )
    channel = joined[0]["id"]

    # Marked like everything else a run creates, so a message left behind by a
    # crash says which run left it.
    mark = current().name("probe")
    posted = {}
    try:
        posted = await _run(
            holder,
            organization,
            config,
            "chat_post_message",
            {"body": {"channel": channel, "text": f"Lemma scenario suite — {mark}"}},
        )
        assert posted.get("ts"), f"Slack accepted nothing: {posted}"

        history = await _run(
            holder,
            organization,
            config,
            "conversations_history",
            {"channel": channel, "limit": 20},
        )
        texts = [m.get("text") or "" for m in history.get("messages") or []]
        assert any(mark in text for text in texts), (
            f"the message was accepted but is not in the channel's history. "
            f"Slack returned ts={posted.get('ts')}; history holds {len(texts)} messages"
        )
    finally:
        if posted.get("ts"):
            # Unconditionally, so a failed assertion does not leave real
            # messages accumulating in somebody's workspace.
            await _run(
                holder,
                organization,
                config,
                "chat_delete",
                {"body": {"channel": channel, "ts": posted["ts"]}},
            )
