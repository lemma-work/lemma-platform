"""One tool, one name, however the agent calling it spells that name.

Every agent namespaces MCP tools with the server they came from, and each does
it differently. The namespace is collision avoidance and nothing else, so a
reader — a chat card, an icon, an approval, a usage record — has to see the same
`exec_command` whether the call came from a pod agent or from Claude Code
running on someone's laptop.
"""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure.mcp import (
    LEMMA_MCP_SERVER_NAME,
    exported_tool_name,
    is_provider_scoped_lemma_mcp_tool_name,
    normalize_local_mcp_tool_name,
)


class TestNamespacesLemmaOwns:
    @pytest.mark.parametrize(
        "reported",
        [
            "mcp__lemma_tools__lemma_exec_command",
            "mcp.lemma_tools.lemma_exec_command",
            "lemma_tools.lemma_exec_command",
            "lemma_tools_lemma_exec_command",
            # Names older builds produced: a hyphenated server, and the Agent
            # Host's own `lemma` before it registered the run's published name.
            "mcp__lemma-tools__lemma_exec_command",
            "mcp__lemma__lemma_exec_command",
            "lemma__lemma_exec_command",
            # Already canonical, with and without the tool prefix.
            "lemma_exec_command",
            "exec_command",
        ],
    )
    def test_every_spelling_is_the_same_tool(self, reported: str) -> None:
        assert normalize_local_mcp_tool_name(reported) == "exec_command"

    def test_the_published_server_name_round_trips(self) -> None:
        """What a run publishes and what a name normalizes from are one thing."""
        wire_name = f"mcp__{LEMMA_MCP_SERVER_NAME}__{exported_tool_name('read_table')}"

        assert normalize_local_mcp_tool_name(wire_name) == "read_table"


class TestNamespacesLemmaDoesNotOwn:
    def test_a_third_party_mcp_tool_keeps_its_name(self) -> None:
        assert (
            normalize_local_mcp_tool_name("mcp__github__create_issue")
            == "mcp__github__create_issue"
        )
        assert not is_provider_scoped_lemma_mcp_tool_name("mcp__github__create_issue")

    def test_someone_elses_server_named_after_us_is_still_theirs(self) -> None:
        """The server name is matched whole. `lemma-corp` is not `lemma`."""
        assert (
            normalize_local_mcp_tool_name("mcp__lemma-corp__delete_everything")
            == "mcp__lemma-corp__delete_everything"
        )
        assert not is_provider_scoped_lemma_mcp_tool_name(
            "lemma-corp.delete_everything"
        )

    def test_a_word_starting_with_lemma_is_not_a_namespace(self) -> None:
        """The server name has to be followed by a separator. Without that check
        `lemmatize_text` reads as the `lemma` server's `tize_text`."""
        assert normalize_local_mcp_tool_name("lemmatize_text") == "lemmatize_text"
        assert not is_provider_scoped_lemma_mcp_tool_name("lemmatize_text")

    def test_a_native_tool_is_left_alone(self) -> None:
        for native in ("WebSearch", "Bash", "read_file"):
            assert normalize_local_mcp_tool_name(native) == native
