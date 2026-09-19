"""Where a workspace's files live, named once.

In ``sandbox_runtime`` rather than in the workspace module because both sides
need it and only this direction is allowed: the code that runs *inside* a
sandbox cannot import from ``app``, while ``app`` already imports this package's
protocol. Put it the other way round and the two would drift -- which they have,
repeatedly, in exactly this area: the default was written out separately in the
process manager, the filesystem manager, the python session manager and the
runtime's own app factory, and the containment message named a root that one of
them did not enforce.

There are two roots here and they are not the same question. ``HOME_ROOT`` is
what *survives*; ``WORKSPACE_ROOT`` is where projects *go*. On a fabric where the
sandbox is the disk they are the same disk and the distinction costs nothing. On
Docker and ``lemma_local`` it is load-bearing: the volume is the only durable
object, so it is mounted at the home and everything a tool writes to ``~``
survives with it. Mounting it at the project root instead would put ``~/.npm``,
``~/.cargo`` and ``~/.python`` back in the container layer, which is the exact
failure that made the home the durable root in the first place.
"""

from __future__ import annotations

#: The durable root, and the sandbox user's home. Tools put their state in ``~``
#: whether or not anyone planned for it, so making the home the durable thing is
#: what stops each one needing to be redirected by hand -- which is how
#: ``PNPM_HOME`` came to point into the volume on one fabric and into the home
#: directory on the other.
HOME_ROOT = "/home/user"

#: Where conversations and projects are created. Inside the home, so it inherits
#: its durability, and named ``lemma`` so that it is the same path on both sides
#: of a host-dispatched run: Agent Host already maps a sandbox directory onto
#: ``~/lemma`` on the user's own machine.
WORKSPACE_ROOT = f"{HOME_ROOT}/lemma"

#: Everything a workspace operation may address. The home rather than the
#: project root, because a sandbox belongs to one user and browsing their own
#: ``~/.config`` is not a boundary worth enforcing -- the shell can already read
#: it. ``/tmp`` is here because the runtime genuinely allows it -- session-scoped
#: credentials are staged there precisely so they die with the sandbox -- and it
#: is deliberately *not* reachable through the HTTP files route, which is a
#: narrower surface than a shell. See ``api/controllers/files_controller``.
RUNTIME_FILESYSTEM_ROOTS = (HOME_ROOT, "/tmp")


def is_inside_home(path: str) -> bool:
    """Whether this absolute path is under the durable root.

    The containment question the HTTP files route asks. ``/tmp`` is allowed by
    the runtime and refused here, which is the whole difference between the two
    surfaces.
    """
    return path == HOME_ROOT or path.startswith(f"{HOME_ROOT}/")


__all__ = [
    "HOME_ROOT",
    "RUNTIME_FILESYSTEM_ROOTS",
    "WORKSPACE_ROOT",
    "is_inside_home",
]
