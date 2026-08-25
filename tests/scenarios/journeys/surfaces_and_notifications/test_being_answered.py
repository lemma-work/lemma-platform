"""Surfaces and notifications → a message in, an answer back.

The plainest thing a surface promises, and the one everything else here is
built on top of: somebody writes to the agent where they already are, and reads
what it said in the same place.

Worth its own file because the two lanes disagreed about it for a while, and
the disagreement turned out to be about how each lane *read* the answer rather
than about the answer. Lemma replies with `sendRichMessage`, which carries its
words in `rich_message` and leaves the plain `text` field empty. The forged
lane unwrapped that and saw the words; the live lane read `text` alone and saw
nothing, which was written up as the product delivering unreadable messages.
It does not — a rich message renders as an ordinary bubble on a real client.
Both readers now look in both places.

Kept as a note rather than deleted because the failure mode is general: a lane
that reads a different field than the product writes will report a working
product as broken, and it will do it with a very convincing transcript.
"""

from __future__ import annotations

import base64


from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]

#: A 64×64 solid red PNG, still small enough to inline.
#:
#: It was 1×1, which is a real PNG and which Telegram accepts — and which the
#: model then refused: "the file that came through is only 288 bytes and isn't
#: recognized as a valid image". Telegram re-encodes an inbound photo, and a
#: one-pixel image comes out below what a vision preprocessor will look at. The
#: scenario was asserting that the agent can see a picture while handing it one
#: nothing can see, so a red result would have been luck and the red result it
#: got was a refusal.
#:
#: 64×64 is still trivially and unambiguously red, and survives re-encoding.
A_RED_DOT = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetf"
    "WiP4FgYrsKZeS0BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBA"
    "QEDgsqnc8OJg6Ln3AAAAAElFTkSuQmCC"
)


@scenario("Somebody messages the agent on a surface and is answered there")
@proves("PS-SURF-010", "PS-SURF-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_message_is_answered(reachable):
    await reachable.says("Hello — reply with a short greeting so I know you are there.")

    answer = await reachable.waits_for_a_reply()

    assert answer.text.strip(), (
        "the agent sent an empty message back, which a person reads as the "
        "product being broken"
    )


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
