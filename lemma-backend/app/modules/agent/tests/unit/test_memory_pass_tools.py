"""The memory pass reaches memory and nothing else.

It runs with the agent's authority, so its grants would let it write wherever
the agent could. These hold it to the memory ``## Your Memory`` names.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.tools.pod.memory_pass_tools import (
    MemoryScope,
    memory_pass_toolset,
)
from app.modules.agent.tools.pod.models import PodReadFileRequest, PodWriteFileRequest

_SCOPE = MemoryScope(agent_memory_paths_for_name("pod_default"))


@pytest.mark.parametrize(
    "path",
    [
        "/memory/AGENTS.md",
        "/me/AGENTS.md",
        "/memory/agents/pod-default/AGENTS.md",
        "/memory/agents/pod-default/clients.md",
        "/me/agents/pod-default/studio.md",
        # A pod-shared topic a person wrote by hand, which its index points to.
        "/memory/billing.md",
    ],
)
def test_the_pass_may_write_the_memory_its_agent_is_given(path):
    assert _SCOPE.refusal(path, write=True) is None


@pytest.mark.parametrize(
    "path",
    [
        # Another agent's state, shared or private.
        "/memory/agents/researcher/AGENTS.md",
        "/me/agents/researcher/notes.md",
        # The person's own files are theirs, not memory.
        "/me/report.md",
        "/me/c/2026-10-22/plan/draft.md",
        "/projects/roadmap.md",
        # Relative, or climbing out of memory.
        "notes.md",
        "/memory/../me/report.md",
        "",
    ],
)
def test_the_pass_may_not_write_outside_its_memory(path):
    refused = _SCOPE.refusal(path, write=True)

    assert refused is not None and refused["success"] is False


def test_the_pass_may_read_memory_but_not_the_rest_of_the_pod():
    assert _SCOPE.refusal("/me/agents/researcher/notes.md", write=False) is None
    assert _SCOPE.refusal("/memory/agents/researcher/AGENTS.md", write=False) is None
    assert _SCOPE.refusal("/projects/roadmap.md", write=False) is not None


@pytest.mark.anyio
async def test_a_refused_call_never_reaches_the_datastore():
    """Refused before the agent's tool runs: with no run context at all, the
    wrapped tool would fail on its first line rather than answer."""
    tools = memory_pass_toolset(_SCOPE).tools

    written = await tools["pod_write_file"].function(
        None, PodWriteFileRequest(path="/me/report.md", content="overwritten")
    )
    read = await tools["pod_read_file"].function(
        None, PodReadFileRequest(path="/projects/roadmap.md")
    )

    assert written["success"] is False and "memory files" in written["error"]
    assert read["success"] is False
