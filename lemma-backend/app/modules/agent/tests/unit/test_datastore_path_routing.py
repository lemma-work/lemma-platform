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
    HOME_ROOT,
    RUNTIME_FILESYSTEM_ROOTS,
    WORKSPACE_ROOT,
)


@pytest.mark.parametrize("root", RUNTIME_FILESYSTEM_ROOTS)
def test_every_runtime_root_addresses_the_sandbox(root: str) -> None:
    """Parametrised over the roots themselves, so adding one cannot skip it."""
    assert not is_datastore_path(root)
    assert not is_datastore_path(f"{root}/notes.md")


def test_the_project_root_and_the_rest_of_the_home_are_both_the_sandbox() -> None:
    """Routing asks which disk, not which directory.

    A conversation's own files and the tool caches beside them are both in the
    sandbox; only the project root is this session's *directory*, and that is a
    different question asked elsewhere.
    """
    assert not is_datastore_path(f"{WORKSPACE_ROOT}/c/2026-01-01/slug/out.txt")
    assert not is_datastore_path(f"{HOME_ROOT}/.npm/_cacache")


def test_the_parent_of_the_home_is_not_the_sandbox() -> None:
    """`/home` is not a root, and prefix matching must not make it one.

    The check is `startswith(f"{root}/")` rather than `startswith(root)` for
    this reason: the looser form would also claim `/home/username-of-someone`
    and `/tmpfiles`, and a path the sandbox cannot serve would stop being
    routed at the pod that can.
    """
    parent, _, _ = HOME_ROOT.rpartition("/")
    assert parent and parent != HOME_ROOT
    assert is_datastore_path(parent)
    assert is_datastore_path(f"{HOME_ROOT}-other/notes.md")


def test_a_path_is_routed_by_what_it_names_after_traversal() -> None:
    """A raw prefix check routes `/tmp/../me/report` by its first segment.

    That segment is a runtime root, so it went to the sandbox -- which does not
    have the file, and the agent gets a 404 from the wrong disk rather than the
    report. The decision has to be made against the path the traversal actually
    names, so it is normalised first.
    """
    assert is_datastore_path("/tmp/../me/report")
    assert is_datastore_path(f"{HOME_ROOT}/../../etc/passwd")


def test_traversal_that_stays_inside_a_root_still_addresses_the_sandbox() -> None:
    """Normalising must not send ordinary paths to the pod by accident."""
    assert not is_datastore_path(f"{WORKSPACE_ROOT}/a/../b.txt")
    assert not is_datastore_path(f"{WORKSPACE_ROOT}/")
    assert not is_datastore_path("/tmp/./staged")


def test_a_doubled_leading_slash_still_names_the_sandbox() -> None:
    """`normpath` will not collapse exactly two, which is the reachable case.

    POSIX leaves a leading `//` implementation-defined, so `//tmp/x` survives
    normalisation while `///tmp/x` collapses to `/tmp/x`. Joining a cwd that
    already ends in `/` to an absolute name produces the doubled form, and it
    read as a pod path -- so the file was looked for on the disk that does not
    have it.
    """
    assert not is_datastore_path("//tmp/staged")
    assert not is_datastore_path(f"/{WORKSPACE_ROOT}/a.txt")
    assert not is_datastore_path(f"/{HOME_ROOT}/a.txt")
    assert not is_datastore_path("///tmp/x")


def test_a_doubled_leading_slash_does_not_rescue_a_pod_path() -> None:
    """Collapsing must not drag pod paths over to the sandbox either."""
    assert is_datastore_path("//me/report")
    assert is_datastore_path("///me/report")


def test_pod_paths_address_the_datastore() -> None:
    assert is_datastore_path("/me")
    assert is_datastore_path("/me/reports/q3.md")


def test_a_relative_path_is_the_sandbox_and_is_never_routed_at_the_pod() -> None:
    """Relative paths resolve against the session's cwd, which is in the sandbox."""
    assert not is_datastore_path("notes.md")
    assert not is_datastore_path("./notes.md")
    assert not is_datastore_path("")
