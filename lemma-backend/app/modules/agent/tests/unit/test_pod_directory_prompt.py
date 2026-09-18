"""Does the agent know where a person's attachment actually is?

Observed against a running stack, and twice: an agent asked to read a file that
had just been attached looked in the workspace sandbox -- the only directory the
prompt named -- found nothing, went searching, and either stumbled onto the pod
path after four tool calls or reported that the file did not exist. A named
agent with only the POD toolset got no directory section at all and failed
outright.

Two separate things made that happen, and both are prompt-shaped:

1. Nothing told the agent that `/me/c/<date>/<slug>` exists, or that a relative
   pod path resolves there. `# Working Directory` names `/workspace` and only
   `/workspace`.
2. Search over pod files is an index built *after* the file is stored, so a file
   uploaded moments ago is readable by path while search still returns nothing.
   An agent that looks by searching gets an empty result and concludes the file
   is not there -- which reads as a storage race and is not one.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.prompt_directories import _pod_cwd, _workspace_cwd
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.services.workspace_location import resolve_workspace_location

pytestmark = pytest.mark.unit

POD_CWD = "/me/c/2026-08-30/ab12cd34"
WORKSPACE_CWD = "/workspace/c/2026-08-30/ab12cd34"


def _conversation() -> Conversation:
    # A named agent's conversation, not the pod default's: the pod default runs
    # the full batteries-included toolset and would be told everything whatever
    # this asked for, so it can say nothing about what a toolset gates.
    return Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())


def _agent(conversation: Conversation, toolsets: list[str]) -> Agent:
    return Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="butler",
        instruction="Answer briefly.",
        toolsets=toolsets,
    )


def _instructions(toolsets: list[str]) -> str:
    conversation = _conversation()
    return build_agent_instructions(
        agent=_agent(conversation, toolsets),
        conversation=conversation,
        ctx=SimpleNamespace(pod_cwd=POD_CWD, workspace_cwd=WORKSPACE_CWD),
    )


class TestTheAgentIsToldWherePodFilesAre:
    def test_an_agent_with_only_pod_tools_still_gets_a_directory(self) -> None:
        """The case that failed outright: no workspace toolset, so before this
        the agent was told about no directory whatsoever."""
        text = _instructions(["POD"])
        assert "# Pod Files" in text
        assert POD_CWD in text

    def test_the_workspace_shell_gets_it_too(self) -> None:
        # It reaches pod files through `lemma files`, so it needs the same fact.
        assert POD_CWD in _instructions(["WORKSPACE_CLI"])

    def test_an_agent_that_cannot_reach_pod_files_is_not_told_about_them(self) -> None:
        text = _instructions(["TODO"])
        assert "# Pod Files" not in text

    def test_a_relative_path_is_spelled_out(self) -> None:
        # "report.pdf means /me/c/.../report.pdf" — the agent should not have to
        # infer the resolution rule from the tool schema.
        text = _instructions(["POD"])
        assert f"{POD_CWD}/report.pdf" in text


class TestTheAgentIsToldWhereAttachmentsAre:
    def test_attachments_are_named_as_being_in_the_pod_directory(self) -> None:
        text = _instructions(["POD"])
        assert "attached" in text
        assert "not in the workspace sandbox" in text

    def test_the_workspace_section_points_away_from_itself(self) -> None:
        """Said in `# Working Directory` as well, because that is the section an
        agent acts on when told to go and read something."""
        text = _instructions(["WORKSPACE_CLI"])
        start = text.index("# Working Directory")
        end = text.index("# Pod Files")
        assert "attached to this conversation are not here" in text[start:end]

    def test_the_workspace_section_stays_quiet_without_pod_files(self) -> None:
        # Nowhere else to point at, so it says nothing rather than naming a
        # directory this agent cannot reach.
        assert "attached to this conversation are not here" not in _instructions(
            ["TODO"]
        )


class TestAnEmptySearchIsNotAnAbsentFile:
    def test_the_agent_is_told_search_lags_the_write(self) -> None:
        text = _instructions(["POD"])
        assert "index built after a file is stored" in text.replace("\n", " ")
        assert "still returns nothing for it" in text.replace("\n", " ")

    def test_and_is_told_what_does_answer_the_question(self) -> None:
        text = _instructions(["POD"])
        assert "list the directory or read the path" in text.replace("\n", " ")

    def test_the_lag_is_not_stated_as_a_ban_on_searching(self) -> None:
        """It read as "never search", and two other fragments say to search.

        The rule is about one question — does this specific file exist — and
        overreached into a general prohibition, leaving the same prompt telling
        the agent both things.
        """
        text = _instructions(["POD"]).replace("\n", " ")
        assert "Do not go looking for it with search" not in text
        assert "Search is the right tool" in text


class TestTheDirectoryFallsBackToWhereTheAgentActuallyIs:
    """A run context without a cwd must still name a real directory.

    The prompt's fallback was ``/workspace/conversations/{id}``, a path shape
    this platform stopped making long ago: the cwd is persisted in conversation
    metadata and otherwise defaults to ``/workspace/c/{date}/{slug}``. So the one
    section whose whole job is to say where the agent is named somewhere that
    does not exist -- silently, whenever the context did not carry a cwd.

    Both fallbacks now call the resolver that owns the ladder, so there is one
    answer rather than three.
    """

    def _conversation(self, metadata=None):
        return Conversation(
            id=UUID("00000000-0000-4000-8000-0000000000ab"),
            pod_id=uuid4(),
            user_id=uuid4(),
            metadata=metadata or {},
        )

    def test_the_workspace_fallback_matches_the_resolver(self):
        conversation = self._conversation()
        assert (
            _workspace_cwd(SimpleNamespace(), conversation)
            == resolve_workspace_location(conversation).cwd
        )

    def test_the_workspace_fallback_is_not_the_retired_path_shape(self):
        conversation = self._conversation()
        assert "/workspace/conversations/" not in _workspace_cwd(
            SimpleNamespace(), conversation
        )

    def test_a_persisted_cwd_wins_over_any_default(self):
        """The common case: creation stamps the directory into metadata."""
        conversation = self._conversation({"cwd": "/workspace/c/2026-09-15/ly827dnk"})
        assert (
            _workspace_cwd(SimpleNamespace(), conversation)
            == "/workspace/c/2026-09-15/ly827dnk"
        )

    def test_the_pod_fallback_shares_the_workspace_suffix(self):
        """The two filesystems mirror each other; a second derivation drifts."""
        conversation = self._conversation({"cwd": "/workspace/c/2026-09-15/ly827dnk"})
        assert _pod_cwd(SimpleNamespace(), conversation) == "/me/c/2026-09-15/ly827dnk"

    def test_the_context_still_wins_when_it_carries_one(self):
        conversation = self._conversation({"cwd": "/workspace/c/2026-09-15/ly827dnk"})
        ctx = SimpleNamespace(workspace_cwd="/workspace/somewhere/else")
        assert _workspace_cwd(ctx, conversation) == "/workspace/somewhere/else"
