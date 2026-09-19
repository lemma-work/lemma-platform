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

#: The browser's profile, and therefore where a person's logins live.
#:
#: In the home because that is the durable root, which is the whole point: a
#: sign-in that does not outlive the sandbox is a sign-in the person gets asked
#: for again on the next conversation. The previous design put the profile in
#: ``/tmp`` and reconstructed logins afterwards from a scoped, encrypted copy of
#: the cookies -- which meant guessing which cookies *were* the login, and
#: getting that wrong three separate times. Chrome already knows. Let it keep
#: its own state and there is nothing left to guess.
#:
#: Not ``~/.agent-browser``: that is the CLI's own cache of downloaded browser
#: binaries and scratch sessions, and quiesce still clears it wholesale.
BROWSER_PROFILE_ROOT = f"{HOME_ROOT}/.lemma/browser"

#: The one profile. One per person, because a sandbox is one machine per person
#: and Chrome locks a profile directory -- so "a persistent profile" and "one
#: browser" are the same statement. Parallel isolated browsers are still
#: available by passing ``--session`` with a ``--profile`` of their own, and
#: those stay under ``/tmp`` where they die with the sandbox.
BROWSER_PROFILE = f"{BROWSER_PROFILE_ROOT}/profile"


def is_inside_home(path: str) -> bool:
    """Whether this absolute path is under the durable root.

    The containment question the HTTP files route asks. ``/tmp`` is allowed by
    the runtime and refused here, which is the whole difference between the two
    surfaces.
    """
    return path == HOME_ROOT or path.startswith(f"{HOME_ROOT}/")


def is_browser_private(path: str) -> bool:
    """Whether this path is inside the browser's own profile.

    Refused by the HTTP file routes even though it sits under the durable
    root, which is the one exception to "the shell can read it anyway, so
    the file API may too".

    The profile holds the cookie database and the local-storage LevelDB --
    the live sessions of every site a person has signed in to. The listing
    endpoint goes to some trouble never to return a cookie *value*; serving
    the file it lives in would make that ceremony. Moving the profile from
    `/tmp` into the home is what put it in range, so the exclusion arrives
    with it.

    The shell inside the sandbox can still read it. That was accepted
    deliberately and written down: the agent can already *use* every session
    by driving the browser. What is not accepted is a credential store
    reachable over ordinary HTTP by anything holding a file path.
    """
    return path == BROWSER_PROFILE_ROOT or path.startswith(f"{BROWSER_PROFILE_ROOT}/")


__all__ = [
    "BROWSER_PROFILE",
    "BROWSER_PROFILE_ROOT",
    "HOME_ROOT",
    "RUNTIME_FILESYSTEM_ROOTS",
    "WORKSPACE_ROOT",
    "is_browser_private",
    "is_inside_home",
]
