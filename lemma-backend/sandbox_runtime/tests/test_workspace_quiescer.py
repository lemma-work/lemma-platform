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
async def test_a_suspend_keeps_the_login_and_drops_only_the_dead_locks(
    tmp_path: Path,
) -> None:
    """The profile survives a pause; the files naming a dead process do not.

    This class removes *nonportable compute state*, and it used to read that
    as "the whole browser profile". The distinction it was missing is the one
    that matters to a person: `SingletonLock` names a process that a suspend
    has already ended, so Chrome refuses to start against it and it has to go.
    `Cookies` names nothing -- it is the login, and deleting it is why signing
    in lasted exactly as long as the sandbox did.
    """
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "Cookies").write_text("the session")
    (profile / "Preferences").write_text("{}")
    (profile / "Local Storage").mkdir()
    (profile / "SingletonLock").symlink_to("hostname-1234")
    (profile / "DevToolsActivePort").write_text("41337\n/devtools/browser/x")

    await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
        browser_profile=profile,
        isolated_process_namespace=False,
        shed_browser_processes=lambda: 0,
    ).quiesce()

    assert (profile / "Cookies").read_text() == "the session"
    assert (profile / "Preferences").exists()
    assert (profile / "Local Storage").is_dir()
    # `is_symlink` rather than `exists`: the target never existed, so a
    # dangling link reports `exists() is False` while still being in the way.
    assert not (profile / "SingletonLock").is_symlink()
    assert not (profile / "DevToolsActivePort").exists()


@pytest.mark.asyncio
async def test_a_missing_profile_is_not_an_error(tmp_path: Path) -> None:
    """The common case: nothing has run a browser in this sandbox yet."""
    await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
        browser_profile=tmp_path / "never-created",
        isolated_process_namespace=False,
        shed_browser_processes=lambda: 0,
    ).quiesce()


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
