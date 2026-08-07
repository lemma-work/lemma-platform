from pathlib import Path

import pytest

from sandbox_runtime.workspace.quiescer import WorkspaceQuiescer


@pytest.mark.asyncio
async def test_quiescer_removes_only_declared_ephemeral_state(tmp_path: Path) -> None:
    ephemeral_directory = tmp_path / "browser"
    ephemeral_directory.mkdir()
    (ephemeral_directory / "cookie").write_text("secret")
    ephemeral_file = tmp_path / "browser.json"
    ephemeral_file.write_text("secret")
    durable_file = tmp_path / "workspace.py"
    durable_file.write_text("durable")

    result = await WorkspaceQuiescer(
        ephemeral_directories=(ephemeral_directory,),
        ephemeral_files=(ephemeral_file,),
        isolated_process_namespace=False,
    ).quiesce()

    assert result.terminated_unmanaged_processes == 0
    assert not ephemeral_directory.exists()
    assert not ephemeral_file.exists()
    assert durable_file.read_text() == "durable"
