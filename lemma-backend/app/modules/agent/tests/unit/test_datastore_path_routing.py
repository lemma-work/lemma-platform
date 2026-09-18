"""Which disk an absolute path addresses, which is decided by omission.

``is_datastore_path`` routes at the pod unless the path is under a runtime
filesystem root. That default is the whole reason this file exists: a root the
list forgets is not rejected, it is quietly sent to the other disk, and the
caller gets a 404 from the datastore rather than the file it asked for. Moving
the workspace root is exactly the change that could drop one.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.file_access import is_datastore_path
from sandbox_runtime.paths import (
    LEGACY_WORKSPACE_ROOT,
    RUNTIME_FILESYSTEM_ROOTS,
    WORKSPACE_ROOT,
)


@pytest.mark.parametrize("root", RUNTIME_FILESYSTEM_ROOTS)
def test_every_runtime_root_addresses_the_sandbox(root: str) -> None:
    """Parametrised over the roots themselves, so adding one cannot skip it."""
    assert not is_datastore_path(root)
    assert not is_datastore_path(f"{root}/notes.md")


def test_the_current_and_legacy_workspace_roots_are_both_the_sandbox() -> None:
    """A conversation recorded before the move still reads its own files."""
    assert not is_datastore_path(f"{WORKSPACE_ROOT}/c/2026-01-01/slug/out.txt")
    assert not is_datastore_path(f"{LEGACY_WORKSPACE_ROOT}/c/2026-01-01/slug/out.txt")


def test_the_parent_of_the_workspace_root_is_not_the_sandbox() -> None:
    """`/home` is not a root, and prefix matching must not make it one.

    The check is `startswith(f"{root}/")` rather than `startswith(root)` for
    this reason: the looser form would also claim `/home/username-of-someone`
    and `/tmpfiles`, and a path the sandbox cannot serve would stop being
    routed at the pod that can.
    """
    parent, _, _ = WORKSPACE_ROOT.rpartition("/")
    assert parent and parent != WORKSPACE_ROOT
    assert is_datastore_path(parent)
    assert is_datastore_path(f"{WORKSPACE_ROOT}-other/notes.md")


def test_pod_paths_address_the_datastore() -> None:
    assert is_datastore_path("/me")
    assert is_datastore_path("/me/reports/q3.md")


def test_a_relative_path_is_the_sandbox_and_is_never_routed_at_the_pod() -> None:
    """Relative paths resolve against the session's cwd, which is in the sandbox."""
    assert not is_datastore_path("notes.md")
    assert not is_datastore_path("./notes.md")
    assert not is_datastore_path("")
