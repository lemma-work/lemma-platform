"""Where a workspace's files live, named once.

In ``sandbox_runtime`` rather than in the workspace module because both sides
need it and only this direction is allowed: the code that runs *inside* a
sandbox cannot import from ``app``, while ``app`` already imports this package's
protocol. Put it the other way round and the two would drift -- which they have,
repeatedly, in exactly this area: the default was written out separately in the
process manager, the filesystem manager, the python session manager and the
runtime's own app factory, and the containment message named a root that one of
them did not enforce.

``LEGACY_WORKSPACE_ROOT`` is accepted and never generated. A sandbox that
predates the move still has the user's files under it, and on E2B the sandbox is
the disk -- so the old root is not something to migrate away from on a schedule,
it is something that keeps working for as long as those sandboxes do.
"""

from __future__ import annotations

#: The durable root. Everything new is created under it, and on a fabric where
#: the sandbox is the disk this is simply the sandbox user's home: tools put
#: their state in ``~`` whether or not anyone planned for it, so making the home
#: the durable thing is what stops each one needing to be redirected by hand --
#: which is how ``PNPM_HOME`` came to point into the volume on one fabric and
#: into the home directory on the other.
WORKSPACE_ROOT = "/home/user"

#: What workspaces created before the move use. Read, never written.
LEGACY_WORKSPACE_ROOT = "/workspace"

#: Everything a workspace operation may address. ``/tmp`` is here because the
#: runtime genuinely allows it -- session-scoped credentials are staged there
#: precisely so they die with the sandbox -- and it is deliberately *not*
#: reachable through the HTTP files route, which is a narrower surface than a
#: shell. See ``api/controllers/files_controller``.
RUNTIME_FILESYSTEM_ROOTS = tuple(
    dict.fromkeys((WORKSPACE_ROOT, LEGACY_WORKSPACE_ROOT, "/tmp"))
)


#: The roots that hold a user's files, newest first. ``/tmp`` is deliberately
#: absent: the runtime allows it, the HTTP files route does not, and a caller
#: asking "which workspace root is this under" never means ``/tmp``.
WORKSPACE_ROOTS = tuple(dict.fromkeys((WORKSPACE_ROOT, LEGACY_WORKSPACE_ROOT)))


def root_of(path: str) -> str | None:
    """Which workspace root this absolute path is under, or None.

    Answering with the root rather than a bool is what lets a caller keep a
    path where it actually is. A workspace created before the move has the
    user's files under the previous root, and re-rooting its paths onto the
    current one does not move the files -- it just points somewhere empty.
    """
    for root in WORKSPACE_ROOTS:
        if path == root or path.startswith(f"{root}/"):
            return root
    return None


def is_legacy_root(path: str) -> bool:
    """Was this path created under the root workspaces used before the move?"""
    return path == LEGACY_WORKSPACE_ROOT or path.startswith(f"{LEGACY_WORKSPACE_ROOT}/")


__all__ = [
    "LEGACY_WORKSPACE_ROOT",
    "WORKSPACE_ROOTS",
    "root_of",
    "RUNTIME_FILESYSTEM_ROOTS",
    "WORKSPACE_ROOT",
    "is_legacy_root",
]
