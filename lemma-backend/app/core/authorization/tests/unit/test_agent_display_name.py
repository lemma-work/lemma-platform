"""The pod's own agent is called Lem, wherever a person can read it.

It answers to three stored spellings and no name at all, and every one of them
used to reach a person somewhere: `pod_default` in a notification header and an
email `From`, `Lemma` -- the *product* -- as the username a chat bot posted
under. The frontend has mapped this since the name was chosen; the backend had
the mapping written out four times, in two different values, none of them
next to the wire name they were mapping from.
"""

from __future__ import annotations

import pytest

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    DEFAULT_RESPONDER_NAME,
    POD_DEFAULT_AGENT_SELECTOR,
    agent_display_name,
)


@pytest.mark.parametrize(
    "stored",
    [
        DEFAULT_POD_AGENT_NAME,  # the `agents` row
        POD_DEFAULT_AGENT_SELECTOR,  # what an API caller and a token say
        None,  # a surface with no agent: the pod answering
        "",
    ],
)
def test_every_spelling_of_the_pod_agent_reads_as_lem(stored):
    assert agent_display_name(stored) == DEFAULT_RESPONDER_NAME


def test_a_named_agent_keeps_its_own_name():
    """The mapping is for one actor, not a rename of every agent."""
    assert agent_display_name("triage") == "triage"


def test_the_display_name_is_not_the_product_name():
    """`Lem` is an instance; `Lemma` is what the bot and the domain already say.

    The two are a word apart and the difference is the whole point: a From line
    of "Lemma (Deepak Jha) via Lemma" names the product twice and the actor
    never, which is why the composer had to special-case it.
    """
    assert agent_display_name(DEFAULT_POD_AGENT_NAME) == "Lem"


def test_the_frontend_agrees():
    """`lemma-frontend/lib/utils/agents.ts` maps the same value to the same name.

    Someone reading a name in Slack and someone reading it in the app are
    reading about the same actor. The two constants are compared here rather
    than trusted to a comment, because they drifted once already -- for eleven
    days, in the direction of the product name.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[5].parent / (
        "lemma-frontend/lib/utils/agents.ts"
    )
    if not source.is_file():
        pytest.skip("frontend checkout is not present")
    assert f"DEFAULT_RESPONDER_NAME = '{DEFAULT_RESPONDER_NAME}'" in source.read_text()
