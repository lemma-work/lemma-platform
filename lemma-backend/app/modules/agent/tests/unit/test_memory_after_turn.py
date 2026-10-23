"""Which runs leave memory to the pass after them, and what each is told.

A run a person is waiting on answers first and has its memory kept by a pass
once it ends; every other run keeps saving memory in the turn. The rule is one
predicate, and the prompt it selects differs in one section -- these hold both
to that, so the two prompts cannot drift apart anywhere else.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.capabilities.memory import MemoryCapability
from app.modules.agent.domain.agent_memory_paths import memory_kept_after_turn
from app.modules.agent.domain.prompts import (
    FRAGMENT_BY_TOOLSET,
    load_memory_pass_prompt,
    load_memory_prompt,
)
from app.modules.agent.domain.value_objects import AgentToolset

_WAITED_ON = {
    "memory_enabled": True,
    "in_process": True,
    "answers_outsiders": False,
}


def _sections(text: str) -> dict[str, str]:
    """The prompt's ``###`` sections by title, the text before the first as ''."""
    sections: dict[str, str] = {}
    title = ""
    for line in text.splitlines():
        if line.startswith("### "):
            title = line.removeprefix("### ").strip()
        sections[title] = sections.get(title, "") + line + "\n"
    return {name: body.strip() for name, body in sections.items()}


@pytest.mark.parametrize("origin", ["chat", "surface"])
def test_a_run_a_person_waits_on_leaves_its_memory_to_the_pass(origin):
    assert memory_kept_after_turn(**_WAITED_ON, origin=origin)


@pytest.mark.parametrize(
    "origin", ["schedule", "workflow", "trigger", "subagent", "pod_ask"]
)
def test_a_run_nobody_waits_on_keeps_saving_memory_in_the_turn(origin):
    assert not memory_kept_after_turn(**_WAITED_ON, origin=origin)


@pytest.mark.parametrize(
    "condition",
    [
        {"memory_enabled": False},
        # The pass runs on the run's own model; an Agent Host coding agent
        # gives Lemma none to run it on.
        {"in_process": False},
        {"answers_outsiders": True},
    ],
)
def test_a_chat_run_keeps_saving_in_the_turn_when_the_pass_cannot_follow_it(
    condition,
):
    assert not memory_kept_after_turn(**{**_WAITED_ON, **condition}, origin="chat")


def test_the_after_turn_contract_differs_only_in_when_to_write():
    in_turn = _sections(load_memory_prompt())
    after_turn = _sections(load_memory_prompt(after_turn=True))

    assert in_turn.keys() == after_turn.keys()
    changed = {name for name in in_turn if in_turn[name] != after_turn[name]}
    assert changed == {"When to write"}
    assert "in the same turn" in in_turn["When to write"]
    assert "only when the person asks you to" in after_turn["When to write"]
    # Telling a person "noted" for a fact nobody wrote would be a lie.
    assert "never tell the person you noted" in after_turn["When to write"]


def test_an_agent_host_run_is_still_told_to_save_in_the_turn():
    """Only the in-process harness has a pass after it, so the fragment the
    remote harness reads is the contract that saves in the turn."""
    remote = FRAGMENT_BY_TOOLSET[AgentToolset.MEMORY].read_text().strip()

    assert remote == load_memory_prompt()
    assert remote != load_memory_prompt(after_turn=True)


def test_the_pass_is_told_how_to_keep_memory_but_not_when():
    pass_prompt = load_memory_pass_prompt()
    contract = _sections(load_memory_prompt())

    for name in ("What is worth keeping", "How to write it", "Memory is reference"):
        assert contract[name] in pass_prompt
    assert "When to write" not in pass_prompt
    assert contract["When to write"] not in pass_prompt
    assert "reply `nothing`" in pass_prompt


@pytest.mark.anyio
@pytest.mark.parametrize("after_turn", [False, True])
async def test_the_assembled_run_is_told_the_contract_its_context_chose(after_turn):
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling

    ctx = SimpleNamespace(
        conversation_id=uuid4(),
        is_pod_default_agent=True,
        memory_enabled=True,
        memory_after_turn=after_turn,
    )
    capabilities = await build_lemma_harness_tooling(
        ctx=ctx, full_toolsets=[], enable_prompt_caching=False
    )

    taught = [
        cap.get_instructions()
        for cap in capabilities
        if isinstance(cap, MemoryCapability)
    ]
    assert taught == [load_memory_prompt(after_turn=after_turn)]
