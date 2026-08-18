"""A workspace path resolves the same whether it is written relative or absolute.

Every tool that takes a workspace path documents both forms -- `listen` says it
accepts "a pod datastore path or a workspace path", `view_image` the same. Only
the relative form worked. `path.lstrip("/")` turns
`/workspace/conversations/<id>/probe.wav` into `workspace/conversations/...`,
which joined onto a root that already ends in exactly that produced
`/workspace/conversations/<id>/workspace/conversations/<id>/probe.wav`, and the
file was reported missing.

It affected every method on the manager, not just the one where it was noticed.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


def _manager(cwd: str) -> WorkspaceFileManager:
    manager = WorkspaceFileManager.__new__(WorkspaceFileManager)
    manager.cwd = cwd
    return manager


CWD = "conversations/01a01397-f051-7303-a4ef-a4ae8781f49a"
ROOT = f"/workspace/{CWD}"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("probe.wav", f"{ROOT}/probe.wav"),
        (f"{ROOT}/probe.wav", f"{ROOT}/probe.wav"),
        ("sub/dir/x.txt", f"{ROOT}/sub/dir/x.txt"),
        (f"{ROOT}/sub/dir/x.txt", f"{ROOT}/sub/dir/x.txt"),
        ("", ROOT),
    ],
)
def test_both_spellings_of_one_path_resolve_to_it(given, expected):
    assert _manager(CWD)._workspace_path(given) == expected


def test_an_absolute_path_is_not_joined_onto_the_root_twice():
    """The exact shape of the bug, named so a regression is unmistakable."""
    resolved = _manager(CWD)._workspace_path(f"{ROOT}/probe.wav")
    assert "workspace/conversations" in resolved
    assert resolved.count("/workspace/") == 1
    assert CWD in resolved
    assert resolved.count(CWD) == 1


def test_another_conversations_path_is_refused_not_quietly_re_homed():
    """Previously this was silently rewritten under the caller's own root.

    The escape guard only ever saw the doubled path, so it could not fire: a
    path naming somebody else's conversation was turned into one naming the
    caller's and read from there. Refusing is the point of the guard.
    """
    other = "/workspace/conversations/00000000-0000-0000-0000-000000000000/secret.txt"
    with pytest.raises(ValueError, match="escapes its configured root"):
        _manager(CWD)._workspace_path(other)


def test_traversal_is_still_refused():
    with pytest.raises(ValueError, match="escapes its configured root"):
        _manager(CWD)._workspace_path("../../etc/passwd")


def test_a_rootless_session_still_resolves_both_forms():
    manager = _manager("")
    assert manager._workspace_path("a.txt") == "/workspace/a.txt"
    assert manager._workspace_path("/workspace/a.txt") == "/workspace/a.txt"
    assert manager._workspace_path("/workspace") == "/workspace"
