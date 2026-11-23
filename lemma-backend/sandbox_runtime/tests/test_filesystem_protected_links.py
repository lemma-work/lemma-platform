"""The browser profile is protected however a path reaches it.

The API refuses a write to the profile by the path as spelled, so a link --
``~/lemma/p -> ~/.lemma/browser/profile`` -- walked a write, a delete, a move
and a sync straight past it. The runtime checks the resolved path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_runtime.workspace.filesystem_manager import (
    FilesystemManager,
    ProtectedPathError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def home(tmp_path: Path) -> dict[str, Path]:
    browser = tmp_path / ".lemma" / "browser"
    profile = browser / "profile"
    profile.mkdir(parents=True)
    (profile / "Cookies").write_bytes(b"session")
    work = tmp_path / "lemma"
    work.mkdir()
    (work / "p").symlink_to(profile)
    (work / "b").symlink_to(browser)
    return {"root": tmp_path, "profile": profile, "work": work, "browser": browser}


def _files(home: dict[str, Path]) -> FilesystemManager:
    return FilesystemManager(
        (str(home["root"]),), protected_paths=(str(home["browser"]),)
    )


async def test_a_write_through_a_link_is_refused(home) -> None:
    with pytest.raises(ProtectedPathError):
        await _files(home).write(
            str(home["work"] / "p" / "Cookies"), b"forged", expected_sha256=None
        )
    assert (home["profile"] / "Cookies").read_bytes() == b"session"


async def test_a_delete_through_a_link_is_refused(home) -> None:
    with pytest.raises(ProtectedPathError):
        await _files(home).delete(str(home["work"] / "b" / "profile"), recursive=True)
    assert (home["profile"] / "Cookies").exists()


async def test_a_move_out_of_or_into_the_profile_through_a_link_is_refused(
    home,
) -> None:
    files = _files(home)
    with pytest.raises(ProtectedPathError):
        await files.move(
            str(home["work"] / "p" / "Cookies"), str(home["work"] / "stolen")
        )
    (home["work"] / "planted").write_bytes(b"x")
    with pytest.raises(ProtectedPathError):
        await files.move(
            str(home["work"] / "planted"), str(home["work"] / "p" / "Cookies")
        )
    assert (home["profile"] / "Cookies").read_bytes() == b"session"


async def test_a_directory_made_through_a_link_is_refused(home) -> None:
    with pytest.raises(ProtectedPathError):
        await _files(home).create_directory(str(home["work"] / "p" / "Default"))


async def test_a_read_through_a_link_is_refused(home) -> None:
    from sandbox_runtime.protocol import ByteRange

    with pytest.raises(ProtectedPathError):
        await _files(home).read(
            str(home["work"] / "p" / "Cookies"), ByteRange(offset=0, length=None)
        )


async def test_removing_the_link_itself_leaves_the_profile_alone(home) -> None:
    assert await _files(home).delete(str(home["work"] / "p"), recursive=False)
    assert (home["profile"] / "Cookies").read_bytes() == b"session"


async def test_ensuring_the_home_that_holds_the_profile_is_allowed(home) -> None:
    """The runtime makes the home (and ``~/.lemma``) exist for every session;
    holding the profile is not touching it."""
    files = _files(home)
    await files.create_directory(str(home["root"]))
    await files.create_directory(str(home["root"] / ".lemma"))
    with pytest.raises(ProtectedPathError):
        await files.delete(str(home["root"] / ".lemma"), recursive=True)


async def test_ordinary_files_beside_it_are_unaffected(home) -> None:
    files = _files(home)
    target = str(home["work"] / "notes.md")
    await files.write(target, b"hi", expected_sha256=None)
    await files.move(target, str(home["work"] / "moved.md"))
    assert await files.delete(str(home["work"] / "moved.md"), recursive=False)


async def test_the_runtime_answers_a_protected_path_with_423(tmp_path) -> None:
    """Not 422, which the API reads as a file too large, and not 403, which
    it reads as its own credential refused -- QA found the first."""
    import json

    from sandbox_runtime.workspace.app import create_app
    from sandbox_runtime.workspace.filesystem_manager import ProtectedPathError

    app = create_app(token="t" * 32, allowed_roots=(str(tmp_path),))
    handler = app.exception_handlers[ProtectedPathError]

    response = await handler(None, ProtectedPathError("the browser's profile"))

    assert response.status_code == 423
    assert "profile" in json.loads(response.body)["detail"]
