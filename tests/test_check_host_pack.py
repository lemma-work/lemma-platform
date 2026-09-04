"""The checker that decides whether a built host pack is shippable.

It had no tests at all, and it is about to run inside the release job on both
macOS and Windows -- so a bug in the checker fails a release rather than
catching one. Everything here builds a fake pack on disk; nothing executes an
interpreter, so it runs anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_host_pack import CONTRACT, check_derived, resolve  # noqa: E402


def pack_with(tmp_path: Path, *, empty: set[str] = frozenset()) -> Path:
    """Every derived path the contract names, non-empty unless asked."""
    pack = tmp_path / "local-runtime"
    for entry in CONTRACT["derived"]:
        path = pack / entry["path"]
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("" if entry["path"] in empty else "content\n")
        else:
            path.mkdir(parents=True, exist_ok=True)
            if entry["path"] not in empty:
                (path / "something").write_text("content\n")
    return pack


def test_a_complete_pack_passes(tmp_path: Path) -> None:
    check_derived(pack_with(tmp_path))


def test_a_missing_path_is_named(tmp_path: Path) -> None:
    pack = pack_with(tmp_path)
    (pack / "release.json").unlink()
    with pytest.raises(SystemExit) as raised:
        check_derived(pack)
    assert "release.json" in str(raised.value)


@pytest.mark.parametrize(
    "path",
    [
        "backend/assets/browser-sdk/lemma-client.js",
        "backend/alembic.ini",
        "release.json",
    ],
)
def test_a_zero_byte_file_is_refused(tmp_path: Path, path: str) -> None:
    """Presence is the one thing that is never the problem.

    A truncated `alembic.ini` or a zero-byte browser bundle passes an existence
    check and produces an install that gets further before it fails, which is
    worse than one that fails here.
    """
    with pytest.raises(SystemExit) as raised:
        check_derived(pack_with(tmp_path, empty={path}))
    assert "is empty" in str(raised.value)
    assert path in str(raised.value)


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        check_derived(pack_with(tmp_path, empty={"backend/migrations"}))
    assert "migrations" in str(raised.value)


def test_the_windows_interpreter_names_are_resolvable(tmp_path: Path) -> None:
    """The release runs this on windows-latest, where the names differ.

    `bin/python3` does not exist there and `python.exe` does -- third in the
    candidate list, so this proves `resolve` reaches it rather than stopping at
    the first miss.
    """
    pack = tmp_path / "local-runtime"
    for relative in ("backend/python/python.exe", "frontend/node/node.exe"):
        path = pack / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    assert resolve(pack, "backend Python").name == "python.exe"
    assert resolve(pack, "frontend Node.js").name == "node.exe"


def test_a_missing_required_path_names_every_candidate(tmp_path: Path) -> None:
    # "Not found" without the list is a puzzle for whoever reads the release log.
    with pytest.raises(SystemExit) as raised:
        resolve(tmp_path, "backend Python")
    message = str(raised.value)
    for candidate in ("backend/python/bin/python3", "backend/python/python.exe"):
        assert candidate in message


def test_every_contract_entry_is_one_the_checker_understands() -> None:
    # A `what` renamed in the contract and not here raises a bare StopIteration
    # from `next()`, which fails a release with no message worth reading.
    for entry in CONTRACT["required"]:
        assert resolve.__doc__  # the function exists
        assert entry["candidates"], f"{entry['what']} promises no path"
    for entry in CONTRACT["derived"]:
        assert "must_not_be_empty" in entry, entry["what"]
        assert "named_by_consumer" in entry, entry["what"]
