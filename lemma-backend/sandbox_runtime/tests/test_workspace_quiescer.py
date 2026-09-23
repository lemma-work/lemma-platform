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
        # A close that worked, which is the ordinary path and the only one
        # in which the browser's runtime may be swept at all. The failed
        # close has its own test below.
        shed_browser_processes=lambda: 1,
    ).quiesce()

    assert result.terminated_unmanaged_processes == 1
    assert result.swept_browser_runtime is True
    assert not ephemeral_directory.exists()
    assert not ephemeral_file.exists()
    assert durable_file.read_text() == "durable"


@pytest.mark.asyncio
async def test_a_suspend_leaves_the_whole_profile_alone(tmp_path: Path) -> None:
    """A pause touches nothing in the browser's profile. Not the login, and
    not the lock files either.

    This began by deleting the whole profile, which is why signing in lasted
    exactly as long as the sandbox did. It was then narrowed to the four
    files naming a dead process -- `SingletonLock`, `SingletonSocket`,
    `SingletonCookie`, `DevToolsActivePort` -- on the theory that Chrome
    would refuse to start against them.

    Measured on a real sandbox, it does not: `kill -9` the browser, leave all
    four behind, and `agent-browser open` starts one and rewrites them, because
    Chrome checks whether the pid a lock names is still alive. And deleting
    them was never free -- `DevToolsActivePort` is the only record of a
    running browser's port, so removing it under a live browser left one that
    nothing could find.
    """
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "Cookies").write_text("the session")
    (profile / "Local Storage").mkdir()
    (profile / "SingletonLock").symlink_to("hostname-1234")
    (profile / "DevToolsActivePort").write_text("41337\n/devtools/browser/x")

    await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
        isolated_process_namespace=False,
        shed_browser_processes=lambda: 0,
    ).quiesce()

    assert (profile / "Cookies").read_text() == "the session"
    assert (profile / "Local Storage").is_dir()
    # `is_symlink` rather than `exists`: the target never existed, so a
    # dangling link reports `exists() is False` while still being present.
    assert (profile / "SingletonLock").is_symlink()
    assert (profile / "DevToolsActivePort").exists()


@pytest.mark.asyncio
async def test_a_missing_profile_is_not_an_error(tmp_path: Path) -> None:
    """The common case: nothing has run a browser in this sandbox yet."""
    await WorkspaceQuiescer(
        ephemeral_directories=(),
        ephemeral_files=(),
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
        shed_browser_processes=lambda: sheds.append(1) or True,
    ).quiesce()

    assert sheds == [1], "the browser must be ended, not just its profile deleted"
    # One, not a process count. Shedding is a single close through the daemon
    # now; the number this used to carry was of processes matched by a
    # pattern list that had gone stale and matched almost none of them.
    assert result.terminated_unmanaged_processes == 1


@pytest.mark.asyncio
async def test_an_isolated_namespace_still_closes_the_browser(monkeypatch) -> None:
    """Docker's sweep does not cover the browser, whatever it looks like.

    This test used to assert the opposite -- "the sweep already covers it,
    doing both would double count" -- and that premise is what made Docker
    the one fabric where a suspend lost the login. The sweep signals, and a
    signal is exactly what loses the session: `agent-browser` runs Chrome on
    a throwaway profile under `/tmp` and copies it back to the durable one
    only on a clean close. Measured on this image, one second after a login:
    a graceful close keeps the session, SIGTERM to all eleven Chrome
    processes does not, and nor does SIGTERM to the browser process alone
    even when it exits cleanly in half a second.

    So the close runs first on every fabric and the sweep still follows. The
    real sweep signals every pid it can see, so it is stubbed rather than
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

    assert sheds == [1], "the browser must be closed before the sweep signals it"


@pytest.mark.asyncio
async def test_a_browser_that_would_not_close_keeps_its_runtime(tmp_path: Path) -> None:
    """The sandbox that came back with a browser that could not start.

    Every ephemeral *directory* here is the agent-browser daemon's runtime --
    its socket, its pid file, the target it is attached to. Deleting that is
    safe before a suspend, because a suspend ends every process. It is not
    safe when the close failed and nothing was terminated, which is reachable
    on the fabric with no blanket sweep: the daemon lives on with its runtime
    directory gone.

    Reproduced on the workspace image before this was written -- delete these
    while Chrome and the daemon are alive and the next `agent-browser open`
    cannot start Chrome at all. Leaving them costs nothing, because they are
    ephemeral by construction and the next start rewrites them.
    """
    runtime = tmp_path / "browser"
    runtime.mkdir()
    (runtime / "workspace.sock").write_text("")
    x_lock = tmp_path / "X99-lock"
    x_lock.write_text("1234")

    result = await WorkspaceQuiescer(
        ephemeral_directories=(runtime,),
        ephemeral_files=(x_lock,),
        isolated_process_namespace=False,
        shed_browser_processes=lambda: 0,
    ).quiesce()

    assert result.swept_browser_runtime is False
    assert runtime.exists(), "a live daemon must keep the runtime it is using"
    # The X lock is not the browser's, and one naming a dead process really
    # does stop the next Xvfb, so it goes either way.
    assert not x_lock.exists()
