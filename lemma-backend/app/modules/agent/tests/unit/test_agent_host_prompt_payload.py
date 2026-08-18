"""What a dispatched run's prompt has to carry, and when.

A Lemma conversation maps to one provider session, kept in one working
directory, so the prompt is just the latest user message: the agent loads the
rest back itself. Sending more would duplicate the conversation in its context.

The exception is a harness that never advertised ``loadSession``. There is no
session to resume there, ever, and one lone message leaves the agent answering
a follow-up it has never seen the start of. It costs nothing in the usual case:
a resumable harness only lacks a stored session on a conversation's first turn,
where there is no history to send.
"""

from __future__ import annotations

from uuid import uuid7

import pytest

from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentToolset,
    ConversationStatus,
    ConversationType,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt
from app.modules.agent.infrastructure.harnesses.remote_payload import run_start_payload
from app.modules.agent.tools.context import BaseAgentContext


pytestmark = pytest.mark.asyncio

POD_ID = uuid7()
CONVERSATION_ID = uuid7()


def _agent() -> Agent:
    return Agent(
        id=uuid7(),
        pod_id=POD_ID,
        user_id=uuid7(),
        name="helper",
        instruction="Be brief.",
    )


def _conversation() -> Conversation:
    return Conversation(
        id=CONVERSATION_ID,
        pod_id=POD_ID,
        user_id=uuid7(),
        agent_id=uuid7(),
        title="continuity",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
    )


def _message(sequence: int, role: str, text: str) -> Message:
    return Message(
        id=uuid7(),
        conversation_id=CONVERSATION_ID,
        sequence=sequence,
        role=role,
        kind=MessageKind.TEXT,
        text=text,
    )


def _transcript() -> list[Message]:
    return [
        _message(1, MessageRole.USER, "Book me a table for four."),
        _message(2, MessageRole.ASSISTANT, "Which night?"),
        _message(3, MessageRole.USER, "Friday."),
    ]


def _ctx() -> BaseAgentContext:
    return BaseAgentContext(
        user_id=uuid7(), pod_id=POD_ID, conversation_id=CONVERSATION_ID
    )


def _woke_up(sequence: int, tool_call_id: str) -> Message:
    """The return the wake synthesizes for the snooze it resolved."""
    return Message(
        id=uuid7(),
        conversation_id=CONVERSATION_ID,
        sequence=sequence,
        role=MessageRole.TOOL,
        kind=MessageKind.TOOL_RETURN,
        tool_name="snooze",
        tool_call_id=tool_call_id,
        tool_result={"woke_because": "TIMER", "note_to_self": "check the build"},
    )


def _user_prompt(
    *,
    carries_history: bool,
    messages: list[Message] | None = None,
    resumed_tool_call_id: str | None = None,
) -> str:
    payload = run_start_payload(
        agent=_agent(),
        conversation=_conversation(),
        messages=_transcript() if messages is None else messages,
        ctx=_ctx(),
        agent_run_id=uuid7(),
        runtime_instructions="",
        carries_history=carries_history,
        resumed_tool_call_id=resumed_tool_call_id,
    )
    return str(payload["prompt"]["user_prompt"])


class TestHistory:
    async def test_a_resumable_run_sends_only_the_latest_turn(self):
        """The provider session already holds the rest, and it is loaded back
        from the conversation's own working directory on every turn."""
        prompt = _user_prompt(carries_history=False)

        assert "Friday." in prompt
        assert "Book me a table" not in prompt
        assert "Which night?" not in prompt

    async def test_a_harness_that_cannot_resume_is_told_the_conversation(self):
        """Otherwise the agent answers a follow-up it has never seen the start
        of, on every single turn, for the life of the conversation."""
        prompt = _user_prompt(carries_history=True)

        assert "Book me a table for four." in prompt
        assert "Which night?" in prompt
        assert "Friday." in prompt
        assert prompt.index("Book me a table") < prompt.index("Friday.")


class TestWakingUp:
    """What a run started by a timer says to an agent that already remembers.

    A woken run adds no user message, so "the latest user message" is the
    request that started the task — and the provider session already contains
    it, along with everything the agent did about it. Sending it again does not
    read as "carry on", it reads as the person asking a second time, and the
    agent starts the work over.
    """

    async def test_the_woken_run_is_told_it_woke(self):
        prompt = _user_prompt(
            carries_history=False,
            messages=[*_transcript(), _woke_up(4, "lemma-mcp-1")],
            resumed_tool_call_id="lemma-mcp-1",
        )

        assert "TIMER" in prompt
        assert "check the build" in prompt
        assert "Friday." not in prompt

    async def test_an_ordinary_turn_still_sends_the_latest_message(self):
        prompt = _user_prompt(carries_history=False, resumed_tool_call_id=None)

        assert "Friday." in prompt

    async def test_a_resume_whose_return_is_gone_falls_back(self):
        """History is trimmed by size, so the message may not have survived.

        Re-sending the last user message is a poor prompt but a live one; a run
        dispatched with no prompt at all is an agent asked to do nothing.
        """
        prompt = _user_prompt(
            carries_history=False, resumed_tool_call_id="lemma-mcp-missing"
        )

        assert "Friday." in prompt


class TestCredentials:
    async def test_the_payload_never_carries_runtime_credentials(self):
        """This payload's destination is somebody's laptop."""
        payload = run_start_payload(
            agent=_agent(),
            conversation=_conversation(),
            messages=_transcript(),
            ctx=_ctx(),
            agent_run_id=uuid7(),
            runtime_instructions="",
            carries_history=False,
        )

        assert "runtime_credentials" not in payload


def _system_prompt(*, toolsets: list[AgentToolset] | None = None) -> str:
    agent = _agent()
    if toolsets is not None:
        agent = agent.model_copy(update={"toolsets": toolsets})
    payload = run_start_payload(
        agent=agent,
        conversation=_conversation(),
        messages=_transcript(),
        ctx=_ctx(),
        agent_run_id=uuid7(),
        runtime_instructions=load_agent_host_runtime_prompt(),
        carries_history=False,
    )
    return str(payload["prompt"]["system_prompt"])


class TestTheAgentIsToldWhichDirectoryIsReal:
    """A local coding agent has two working directories and believes the wrong one.

    Agent Host starts the agent as a real OS process in a Lemma scratch
    directory (`scratch/<target>/<conversation>`), while its actual workspace is
    the sandbox reached over MCP. `pwd` answers with the scratch one. Nothing
    said otherwise, so "we want to build this on lemma (but locally), it should
    run on my mac" met an empty directory and did the obvious wrong thing.

    The working-directory section used to be gated on having the workspace
    toolset, which is right for the in-process harness — it has only one
    directory, so with no tools there is nothing to say. A remote harness has
    two either way.
    """

    async def test_a_remote_run_is_told_the_sandbox_is_the_workspace(self) -> None:
        prompt = _system_prompt(toolsets=[AgentToolset.WORKSPACE_CLI])

        assert "# Working Directory" in prompt
        assert "/workspace/" in prompt
        assert "exec_command" in prompt

    async def test_a_remote_run_is_told_its_own_directory_is_not(self) -> None:
        prompt = _system_prompt(toolsets=[AgentToolset.WORKSPACE_CLI])

        assert "the directory this process started in" in prompt
        assert "pwd" in prompt

    async def test_the_users_own_machine_is_ruled_out_in_words(self) -> None:
        """The instruction the runtime prompt exists to carry.

        Not a sandbox boundary — a local agent could reach the whole filesystem
        if it tried. It is the only control there is here, so it has to be
        unambiguous rather than implied.
        """
        prompt = _system_prompt(toolsets=[AgentToolset.WORKSPACE_CLI])

        assert "not yours to use" in prompt
        assert "home directory" in prompt

    async def test_a_remote_run_without_workspace_tools_still_gets_the_warning(
        self,
    ) -> None:
        """The case the old gate missed entirely.

        No workspace toolset used to mean no working-directory section at all,
        which left the agent with a real directory, no correction, and every
        reason to treat it as the workspace.
        """
        prompt = _system_prompt(toolsets=[])

        assert "# Working Directory" in prompt
        assert "scratch space belonging to Lemma" in prompt

    async def test_pod_files_are_named_as_the_third_place(self) -> None:
        """Workspace, pod files, and the user's machine are three things, and
        conflating the first two is how work ends up somewhere nobody looks."""
        prompt = _system_prompt(toolsets=[AgentToolset.WORKSPACE_CLI])

        assert "Pod files" in prompt
        assert "not scratch space" in prompt
