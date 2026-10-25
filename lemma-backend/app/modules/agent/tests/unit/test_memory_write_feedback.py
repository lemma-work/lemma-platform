"""A write into memory is told when it has grown past what is worth reading back."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.services.context_brief import memory as agent_memory_brief
from app.modules.agent.services.context_brief.memory import guaranteed_index_chars
from app.modules.agent.tools.pod.memory_write_feedback import memory_write_warning

pytestmark = pytest.mark.unit

_PATHS = agent_memory_paths_for_name("researcher")
_INDEXES = (
    _PATHS.personal_agent_index,
    _PATHS.personal_index,
    _PATHS.pod_agent_index,
    _PATHS.pod_index,
)


def _index(chars: int) -> str:
    line = "- a remembered fact, one line of it\n"
    return (line * (chars // len(line) + 1))[:chars]


def _warning(path: str, length: int) -> str | None:
    return memory_write_warning(
        path, user_id=uuid4(), agent_name="researcher", content_length=length
    )


class TestAnIndexIsWarnedAtTheRoomItActuallyGets:
    """The section budget is shared: with all four indexes written, each gets a
    quarter of it, well under the per-index cap. The warning fired only past
    the cap, so an index cut on every run was written without a word."""

    def test_four_full_indexes_under_the_cap_are_still_cut_in_the_brief(self):
        contents = {path: _index(1900) for path in _INDEXES}

        blocks = agent_memory_brief._budgeted_blocks(
            agent_memory_brief._scopes(_PATHS), contents
        )

        assert any(
            path not in blocks or contents[path] not in blocks[path]
            for path in _INDEXES
        )

    def test_so_writing_one_of_them_says_so(self):
        assert 1900 < agent_settings.agent_memory_index_max_chars

        warning = _warning(_PATHS.pod_index, 1900)

        assert warning is not None
        assert f"first {guaranteed_index_chars(_PATHS)}" in warning

    def test_an_index_within_its_guaranteed_room_is_never_cut(self):
        room = guaranteed_index_chars(_PATHS)
        contents = {path: _index(room) for path in _INDEXES}

        blocks = agent_memory_brief._budgeted_blocks(
            agent_memory_brief._scopes(_PATHS), contents
        )

        assert set(blocks) == set(_INDEXES)
        assert all(contents[path] in blocks[path] for path in _INDEXES)
        assert _warning(_PATHS.pod_index, room) is None


class TestATopicFileHasASizeItShouldStayUnder:
    """Topic and journal files had no bound at all, and the prompt told agents
    to keep change history -- so they grew append-only, and were read back in
    full whenever they mattered."""

    @pytest.mark.parametrize(
        "path",
        ["/memory/customers/acme.md", f"{_PATHS.personal_agent_folder}/journal.md"],
    )
    def test_a_memory_file_past_the_limit_is_warned(self, path):
        limit = agent_settings.agent_memory_file_max_chars

        assert _warning(path, limit) is None
        assert "past the" in (_warning(path, limit + 1) or "")

    def test_a_file_outside_memory_is_left_alone(self):
        limit = agent_settings.agent_memory_file_max_chars

        assert _warning("/me/reports/q3.md", limit * 10) is None
