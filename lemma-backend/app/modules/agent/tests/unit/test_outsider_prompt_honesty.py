"""A run answering somebody outside the pod is told only what it can do.

The failure this pins: a guest's run in a group had no write tool and saw no
tables, yet told the group "added" and "counted" -- its prompt taught it to
write records, build tables, post to members and keep a working folder, all
withheld from it, and its base prompt said to build missing pieces. Each test
pins one rule:

* the outsider base prompt replaces the teammate's, and says plainly that it
  cannot save anything and must pass a request on, never claim it done;
* no working folder is named;
* the pod and messaging guidance is not given, and nor is the chat
  platform's guidance on showing files, asking and approving;
* the group brief, not a private chat's, in a group, and it says the same.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.capabilities.assembler import _instructions_for
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.surface_prompts import surface_platform_guidance
from app.modules.agent.services.outsider_brief import render_outsider_brief
from app.modules.agent.tools.messaging.pydantic_adapter import messaging_toolset
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset

pytestmark = pytest.mark.unit

_WITHHELD_WORDS = (
    "pod_write_record",
    "pod_write_file",
    "pod_edit_file",
    "channel_post",
    "lemma tables create",
    "Build missing pieces",
    "# Pod Files",
)


def _instructions(audience: Audience) -> str:
    # A guest's conversation names them: that is what narrows its toolsets.
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        contact_id=audience.contact_id,
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="pod_default",
        instruction="",
        toolsets=["POD", "MESSAGING", "WEB_SEARCH"],
    )
    text = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(
            audience=audience,
            pod_cwd="/me/c/2026-10-28/ab12cd34",
            workspace_cwd="/workspace/c/2026-10-28/ab12cd34",
        ),
    )
    # Prompt files wrap their lines; what the model reads is the words.
    return " ".join(text.split())


def test_a_guests_prompt_names_nothing_it_cannot_do():
    text = _instructions(Audience.contact(uuid4()))

    for withheld in _WITHHELD_WORDS:
        assert withheld not in text, withheld
    assert "You cannot save, change, delete or send anything" in text
    assert "Never say it is done" in text


def test_a_members_prompt_still_has_its_folder_and_tools():
    text = _instructions(Audience.member())

    assert "# Pod Files" in text
    assert "You cannot save, change, delete or send anything" not in text


@pytest.mark.parametrize("toolset", [pod_toolset, messaging_toolset])
def test_a_guests_run_is_not_given_the_guidance_for_tools_it_lacks(toolset):
    assert _instructions_for(toolset, answers_outsider=True) is None
    assert _instructions_for(toolset, answers_outsider=False) is not None


def test_the_chat_platforms_delivery_tools_are_not_offered_to_a_guest():
    guest = surface_platform_guidance(
        "TELEGRAM", audience=Audience.contact(uuid4()), in_group=True
    )
    member = surface_platform_guidance("TELEGRAM", in_group=True)

    for tool in ("display_resource", "request_approval", "ask_user", "write_todos"):
        assert tool not in guest, tool
    assert "display_resource" in member


def test_the_group_brief_says_nothing_is_saved_for_them():
    brief = " ".join(
        render_outsider_brief(pod_name="Trips", owner_display_name="Deepa").split()
    )

    assert "You cannot save, change or send anything" in brief
    assert "never say it is done" in brief
    assert "private chat" not in brief
