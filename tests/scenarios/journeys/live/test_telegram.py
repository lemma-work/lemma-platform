"""Live → a real Telegram bot, reached the way a person reaches it.

The fast lane stands Telegram in for, which proves Lemma's half of the
conversation. This proves the other half: a real bot, a real chat, and a
message that really arrives.

What it does *not* do is pretend a test can be the person. Two assumptions in
the version this replaces were simply untrue, and cost two minutes a run to
discover:

- **A bot never sees its own messages.** The old scenario sent as the bot and
  then waited for an update whose sender `is_bot` — a filter Telegram can never
  satisfy, so it could only ever time out.
- **`getUpdates` has one consumer.** Calling it from the scenario took the
  updates the worker's poller was waiting for, and Telegram answered the poller
  with 409 forty-five times in one run. The test broke the product it was
  testing. Nothing here calls `getUpdates`.

So the direction that *can* be driven unattended is outbound, and it is the
stronger half anyway: a bot cannot cold-DM, so a message only leaves Lemma if a
real person really started a conversation with this bot, on this surface, and
Telegram really accepted the send. The person's part happens once, against the
standing surface, rather than once per run — see `tenant.STANDING_REACH`.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import REAL_MODEL, TELEGRAM, needs
from harness.tenant import CONNECTOR_HOLDER, STANDING_REACH

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
    pytest.mark.live,
]

REACH = STANDING_REACH[0]


@scenario("Lemma delivers a real message to a real person on Telegram")
@proves("PS-SURF-010", "PS-SURF-020")
@covers("agent.surface.send")
async def test_a_real_message_reaches_a_real_person(world):
    needs(TELEGRAM, REAL_MODEL)
    holder = await world.person(CONNECTOR_HOLDER)
    pod = await holder.works_in(REACH.pod)

    surfaces = {str(s.get("name")): s for s in await holder.surfaces_in(pod)}
    if REACH.name not in surfaces:
        pytest.skip(
            f"the standing {REACH.platform} surface is not on {REACH.pod!r}. "
            f"Run `make scenarios-provision` once the {REACH.connector} "
            f"account is connected"
        )

    answer = await holder.api.call(
        "POST",
        f"/pods/{pod['id']}/surfaces/{REACH.name}/send",
        json={
            "user_id": str(holder.user_id),
            "message": "Checking in from the scenario suite.",
        },
    )
    if answer.status_code == 404:
        # The one thing a suite cannot do for itself. A bot has no way to open
        # a conversation, so until somebody has messaged it there is nothing to
        # send *through* — and that is a person's job, not a failure.
        pytest.skip(
            "waiting on a person: nobody has messaged the bot yet. Send it one "
            "message from the Telegram account in SCENARIOS_TELEGRAM_HANDLE, "
            "once — the surface stands between runs, so this is not per run"
        )
    assert answer.status_code == 200, (
        f"sending to a reachable person answered {answer.status_code}: {answer.text[:300]}"
    )
    assert answer.json().get("sent") is True, (
        f"Lemma reported the message as not sent: {answer.text[:300]}. "
        f"A false here means Telegram refused it, so nothing reached the chat."
    )
