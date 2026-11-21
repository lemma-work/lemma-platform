"""A plugin's block in a shared memory index survives every agent write.

Plugins mark a block of their own in ``/memory/AGENTS.md`` and ``/me/AGENTS.md``;
agents write those files whole. These hold the rule (keep every complete block
byte for byte, add none) and that each agent write path applies it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.plugin_memory_blocks import (
    check_blocks_kept,
    find_plugin_blocks,
)
from app.modules.agent.tests.unit.test_pod_tools import _patch_services
from app.modules.agent.infrastructure.tools.pod import pod_file_tools as pod_files
from app.modules.agent.infrastructure.tools.pod.memory_pass_tools import (
    MemoryScope,
    memory_pass_specs,
)
from app.modules.agent.infrastructure.tools.pod.models import (
    FileEdit,
    PodEditFileRequest,
    PodWriteFileRequest,
)
from app.modules.datastore.domain.errors import DatastoreFileNotFoundError

TASKS = (
    "<!-- plugin:tasks -->\n"
    "- Tasks is installed. Guide: `/memory/plugins/tasks.md`.\n"
    "<!-- /plugin:tasks -->"
)
MAIL = "<!-- plugin:mail -->\n- Mail is installed.\n<!-- /plugin:mail -->"
INDEX = f"# Memory\n{TASKS}\n{MAIL}\n- Clients: `/memory/clients.md`\n"


class TestFindPluginBlocks:
    def test_each_complete_block_is_found_with_its_markers(self):
        assert find_plugin_blocks(INDEX) == {"tasks": (TASKS,), "mail": (MAIL,)}

    def test_markers_are_found_with_any_spacing(self):
        text = "<!--plugin:tasks-->\nx\n<!--   /plugin:tasks   -->"

        assert find_plugin_blocks(text) == {"tasks": (text,)}

    def test_an_opening_with_no_closing_is_not_a_block(self):
        text = f"<!-- plugin:tasks -->\nlost its end\n{MAIL}\n"

        assert find_plugin_blocks(text) == {"mail": (MAIL,)}

    def test_an_opening_closed_only_after_the_next_opening_is_not_a_block(self):
        text = f"<!-- plugin:tasks -->\norphan\n{TASKS}\n"

        assert find_plugin_blocks(text) == {"tasks": (TASKS,)}

    def test_another_plugins_closing_marker_does_not_close_a_block(self):
        text = "<!-- plugin:tasks -->\nx\n<!-- /plugin:mail -->\n"

        assert find_plugin_blocks(text) == {}


class TestCheckBlocksKept:
    def test_notes_written_around_the_blocks_are_fine(self):
        after = INDEX.replace("- Clients", "- Billing: `/memory/billing.md`\n- Clients")

        assert check_blocks_kept(INDEX, after) is None

    def test_a_block_may_move(self):
        after = f"# Memory\n- Clients: `/memory/clients.md`\n{MAIL}\n\n{TASKS}\n"

        assert check_blocks_kept(INDEX, after) is None

    def test_a_dropped_block_is_named_and_the_fix_said(self):
        problem = check_blocks_kept(INDEX, INDEX.replace(MAIL, ""))

        assert problem is not None
        assert "<!-- plugin:mail -->" in problem
        assert "<!-- plugin:tasks -->" not in problem
        assert "exactly as it is" in problem
        assert "Nothing was written." in problem

    def test_one_changed_character_is_a_change(self):
        after = INDEX.replace("Mail is installed.", "Mail is installed")

        problem = check_blocks_kept(INDEX, after)

        assert problem is not None and "<!-- plugin:mail -->" in problem
        assert "adds a block" not in problem

    def test_respacing_a_marker_is_a_change(self):
        after = INDEX.replace("<!-- /plugin:mail -->", "<!--/plugin:mail-->")

        assert check_blocks_kept(INDEX, after) is not None

    def test_an_agent_may_not_add_a_block(self):
        fake = (
            "<!-- plugin:billing -->\n- Billing is installed.\n<!-- /plugin:billing -->"
        )

        problem = check_blocks_kept(INDEX, INDEX + fake + "\n")

        assert problem is not None
        assert "<!-- plugin:billing -->" in problem
        assert "without the plugin markers" in problem

    def test_an_unclosed_marker_does_not_lock_the_file(self):
        before = "<!-- plugin:tasks -->\nhalf a block\n- Clients\n"

        assert check_blocks_kept(before, "- Clients, rewritten\n") is None

    def test_a_file_with_no_blocks_is_unaffected(self):
        assert check_blocks_kept("- a\n", "- b\n") is None
        assert check_blocks_kept("", "- b\n") is None


def _ctx() -> ToolContext:
    return ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


def _services(current: str | None, *, path: str = "/memory/AGENTS.md"):
    entity = SimpleNamespace(path=path, content_sha256="same", size_bytes=1)
    download = (
        AsyncMock(side_effect=DatastoreFileNotFoundError("missing"))
        if current is None
        else AsyncMock(return_value=(entity, current.encode("utf-8")))
    )
    return SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=download,
            get_file_by_path=AsyncMock(return_value=entity),
            create_file=AsyncMock(return_value=entity),
            resolve_update_file=AsyncMock(return_value=object()),
            write_update_storage=AsyncMock(),
            persist_update_file=AsyncMock(return_value=entity),
            finalize_update_file=AsyncMock(),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )


@pytest.fixture
def services(monkeypatch):
    # The pod tools' one seam for their services, shared with test_pod_tools.
    # The memory-brief cache is left alone: with no cache configured, dropping
    # an entry is a no-op.
    def install(current: str | None, **kwargs: str) -> SimpleNamespace:
        built = _services(current, **kwargs)
        _patch_services(monkeypatch, built)
        return built

    return install


def _written(services: SimpleNamespace) -> bool:
    return bool(
        services.file.create_file.await_count
        or services.file.write_update_storage.await_count
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("index", ["/memory/AGENTS.md", "/me/AGENTS.md"])
async def test_writing_an_index_without_a_block_is_refused(services, index):
    fake = services(INDEX, path=index)

    result = await pod_files.pod_write_file(
        _ctx(), PodWriteFileRequest(path=index, content="# Memory\n- Clients\n")
    )

    assert result["success"] is False
    assert "<!-- plugin:tasks -->" in result["error"]
    assert not _written(fake)


@pytest.mark.asyncio
async def test_writing_an_index_around_its_blocks_goes_through(services):
    fake = services(INDEX)

    result = await pod_files.pod_write_file(
        _ctx(),
        PodWriteFileRequest(path="/memory/AGENTS.md", content=INDEX + "- New note\n"),
    )

    assert result["success"] is True
    assert _written(fake)


@pytest.mark.asyncio
async def test_a_new_index_may_not_start_with_a_made_up_block(services):
    fake = services(None)

    result = await pod_files.pod_write_file(
        _ctx(), PodWriteFileRequest(path="/memory/AGENTS.md", content=INDEX)
    )

    assert result["success"] is False
    assert not _written(fake)


@pytest.mark.asyncio
async def test_other_files_are_not_read_or_held(services):
    """A topic file can quote a block: only the two indexes are guarded."""
    fake = services(INDEX, path="/memory/plugins/notes.md")

    result = await pod_files.pod_write_file(
        _ctx(), PodWriteFileRequest(path="/memory/plugins/notes.md", content="- x\n")
    )

    assert result["success"] is True
    fake.file.download_file_content_by_path.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_edit_inside_a_block_is_refused(services):
    fake = services(INDEX)

    result = await pod_files.pod_edit_file(
        _ctx(),
        PodEditFileRequest(
            path="/memory/AGENTS.md",
            edits=[FileEdit(old_text="Mail is installed.", new_text="Mail: on.")],
        ),
    )

    assert result["success"] is False
    assert "<!-- plugin:mail -->" in result["error"]
    assert not _written(fake)


@pytest.mark.asyncio
async def test_an_edit_outside_the_blocks_goes_through(services):
    fake = services(INDEX)

    result = await pod_files.pod_edit_file(
        _ctx(),
        PodEditFileRequest(
            path="/memory/AGENTS.md",
            edits=[FileEdit(old_text="- Clients", new_text="- Customers")],
        ),
    )

    assert result["success"] is True
    assert _written(fake)


@pytest.mark.asyncio
async def test_the_memory_keepers_write_is_held_to_the_same_rule(services):
    fake = services(INDEX)
    tools = {
        spec.name: spec
        for spec in memory_pass_specs(
            MemoryScope(agent_memory_paths_for_name("pod_default"))
        )
    }

    result = await tools["pod_write_file"].run(
        _ctx(),
        PodWriteFileRequest(path="/memory/AGENTS.md", content="# Memory\n- Clients\n"),
    )

    assert result["success"] is False
    assert "<!-- plugin:tasks -->" in result["error"]
    assert not _written(fake)
