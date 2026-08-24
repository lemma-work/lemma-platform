"""Surfaces and notifications → a message in, an answer back.

The plainest thing a surface promises, and the one everything else here is
built on top of: somebody writes to the agent where they already are, and reads
what it said in the same place.

Worth its own file because it is the scenario where the two lanes disagree, and
that disagreement is the whole reason for running both. Forged, the answer is
read out of the call Lemma made to `api.telegram.org` — and `Said.text` unwraps
`sendRichMessage`'s nested markdown, so the words are plainly there. Live, real
Telegram accepts the very same call with `ok:true` and then renders a message
with no readable text in it. `DEV-SURF-002`.

A stand-in that is kinder than the thing it stands in for will hide exactly
this class of bug, forever, and no amount of care in the fast lane finds it.
The mark below is what keeps that honest: strict, so the day the product is
fixed this fails until somebody deletes it.
"""

from __future__ import annotations

import base64

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.telegram_chat import telegram_is_live

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]

#: A 1×1 red PNG. Small enough to inline, and a real image — Telegram rejects
#: bytes that are not, and the model is being asked to look at it.
A_RED_DOT = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


#: Anything that asserts on the *words* of a reply fails against real Telegram,
#: and for one reason: `sendRichMessage` is accepted with `ok:true` and produces
#: a message carrying no `text` field, so every real client renders it empty.
#: Strict, so the day that is fixed these turn green and fail the build until
#: the mark comes off. Buttons are unaffected — `reply_markup` is ordinary —
#: which is why the native-controls scenarios pass on the same lane.
UNREADABLE_ON_REAL_TELEGRAM = pytest.mark.xfail(
    telegram_is_live(),
    strict=True,
    reason=(
        "DEV-SURF-002 — a plain answer goes out as sendRichMessage, which real "
        "Telegram accepts with ok:true and then renders as an empty message. "
        "The fallback to sendMessage only fires on a 400/404, so it never runs. "
        "The stand-in accepts sendRichMessage and reads the text back out of it, "
        "which is why the forged lane passes this and a person sees nothing."
    ),
)


@UNREADABLE_ON_REAL_TELEGRAM
@scenario("Somebody messages the agent on a surface and is answered there")
@proves("PS-SURF-010", "PS-SURF-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_message_is_answered(reachable):
    await reachable.says(
        "Hello — reply with a short greeting so I know you are there."
    )

    answer = await reachable.waits_for_a_reply()

    assert answer.text.strip(), (
        "the agent sent an empty message back, which a person reads as the "
        "product being broken"
    )


@UNREADABLE_ON_REAL_TELEGRAM
@scenario("An image sent to the agent is looked at, not just noticed")
@proves("PS-SURF-011", "PS-AGENT-030")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_an_image_is_understood(reachable):
    """The agent must read the picture, not the filename.

    Asserted on the colour rather than on any word in particular: an agent that
    only saw "a photo arrived" cannot say what colour it is, and one that read
    the image can.
    """
    # The stand-in serves one fixed CSV for every file id, so there is nothing
    # to look at in the forged lane however the scenario asks for it.
    reachable.only_live("an image the model can actually see")
    needs(MODEL_IS_REAL)

    await reachable.sends_image(
        "dot.png", A_RED_DOT, caption="What colour is this image? Answer in one word."
    )

    answer = await reachable.waits_for_a_reply()

    assert "red" in answer.text.lower(), (
        f"the agent was sent a red image and did not say red: {answer.text[:200]!r}. "
        f"Either the attachment never reached the model, or it was described "
        f"rather than looked at."
    )
