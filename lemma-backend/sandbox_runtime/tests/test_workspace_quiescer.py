from pathlib import Path

import pytest

from sandbox_runtime.workspace.quiescer import WorkspaceQuiescer


async def _no_processes() -> int:
    return 0


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
        # Never the real one: it matches agent-browser and Xvfb, which a
        # developer running this suite plausibly has open.
        shed_browser_processes=lambda: 0,
    ).quiesce()

    assert result.terminated_unmanaged_processes == 0
    assert not ephemeral_directory.exists()
    assert not ephemeral_file.exists()
    assert durable_file.read_text() == "durable"


@pytest.mark.asyncio
async def test_a_shared_namespace_still_sheds_the_browser(tmp_path: Path) -> None:
    """The E2B path, which is the one carrying production.

    The blanket process sweep is gated on an isolated PID namespace because it
    signals everything it can see, and on E2B that includes envd and E2B's own
    services. Only the Docker image sets the flag -- so E2B was left deleting
    the browser's profile directory while Chrome went on running out of it,
    holding 2 GB on a 2 GB sandbox and surviving into the next conversation,
    because a pause here snapshots memory rather than discarding it.
    """
    sheds = []

    result = await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
        isolated_process_namespace=False,
        shed_browser_processes=lambda: sheds.append(1) or 7,
    ).quiesce()

    assert sheds == [1], "the browser must be ended, not just its profile deleted"
    assert result.terminated_unmanaged_processes == 7


@pytest.mark.asyncio
async def test_an_isolated_namespace_does_not_shed_twice(monkeypatch) -> None:
    """Docker's sweep already covers the browser; doing both would double count.

    The real sweep signals every pid it can see, so it is stubbed rather than
    run: a unit test that SIGTERMs the machine it runs on is not a test.
    """
    sheds = []
    monkeypatch.setattr(
        WorkspaceQuiescer, "_terminate_unmanaged_processes", staticmethod(_no_processes)
    )

    await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
        isolated_process_namespace=True,
        shed_browser_processes=lambda: sheds.append(1) or 1,
    ).quiesce()

    assert sheds == []
