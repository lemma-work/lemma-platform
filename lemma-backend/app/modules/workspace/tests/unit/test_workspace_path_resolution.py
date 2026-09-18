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

from uuid import uuid4

import pytest

from sandbox_runtime.paths import LEGACY_WORKSPACE_ROOT, WORKSPACE_ROOT
from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


def _manager(cwd: str) -> WorkspaceFileManager:
    manager = WorkspaceFileManager.__new__(WorkspaceFileManager)
    manager.cwd = cwd
    return manager


CWD = "conversations/01a01397-f051-7303-a4ef-a4ae8781f49a"
ROOT = f"{WORKSPACE_ROOT}/{CWD}"


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
    assert "conversations" in resolved
    assert resolved.count(f"{WORKSPACE_ROOT}/") == 1
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
    assert manager._workspace_path("a.txt") == f"{WORKSPACE_ROOT}/a.txt"
    assert (
        manager._workspace_path(f"{WORKSPACE_ROOT}/a.txt") == f"{WORKSPACE_ROOT}/a.txt"
    )
    assert manager._workspace_path(WORKSPACE_ROOT) == WORKSPACE_ROOT


def test_a_workspace_from_before_the_move_keeps_its_own_root():
    """Its files are under the previous root, and re-rooting does not move them.

    A conversation created before the root moved has that root written into its
    metadata, and those rows are never rewritten. Resolving its paths against
    the *current* root would point at a directory nothing has written to and
    report the user's own work missing.
    """
    manager = WorkspaceFileManager(uuid4(), cwd=f"{LEGACY_WORKSPACE_ROOT}/c/x")

    assert manager.root == LEGACY_WORKSPACE_ROOT
    assert manager._workspace_path("f.txt") == f"{LEGACY_WORKSPACE_ROOT}/c/x/f.txt"
    assert (
        manager._workspace_path(f"{LEGACY_WORKSPACE_ROOT}/c/x/f.txt")
        == f"{LEGACY_WORKSPACE_ROOT}/c/x/f.txt"
    )


def test_a_new_workspace_is_rooted_at_the_current_root():
    manager = WorkspaceFileManager(uuid4(), cwd=f"{WORKSPACE_ROOT}/c/x")

    assert manager.root == WORKSPACE_ROOT
    assert manager._workspace_path("f.txt") == f"{WORKSPACE_ROOT}/c/x/f.txt"


def test_one_workspace_root_cannot_reach_the_other():
    """Tolerating the previous root is not the same as merging the two."""
    manager = WorkspaceFileManager(uuid4(), cwd=f"{WORKSPACE_ROOT}/c/x")

    with pytest.raises(ValueError, match="escapes its configured root"):
        manager._workspace_path(f"{LEGACY_WORKSPACE_ROOT}/c/x/f.txt")


def test_a_cwd_under_no_workspace_root_is_refused():
    for cwd in ("/tmp/elsewhere", "/etc"):
        with pytest.raises(ValueError, match="must be relative"):
            WorkspaceFileManager(uuid4(), cwd=cwd)
