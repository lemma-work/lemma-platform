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

import os
from pathlib import Path

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


#: Where the backend installs the runtime overlay: Lemma's own sandbox code,
#: newer than the copy the image bakes. Outside the home on purpose -- platform
#: code, not the user's files.
RUNTIME_OVERLAY_ROOT = "/opt/lemma-runtime"

#: Where the runtime overlay puts its commands. Named through ``current``, so
#: an upgrade is the installer's symlink flip and nothing here moves.
RUNTIME_OVERLAY_BIN = f"{RUNTIME_OVERLAY_ROOT}/current/bin"

#: A shell prefix putting the overlay's commands first for the rest of one
#: command line. For the commands the backend sends: a sandbox created from an
#: image or template older than the overlay's own `PATH` entry would otherwise
#: run the baked scripts for as long as it lives. An absent directory costs a
#: failed lookup, and the image's copy answers.
OVERLAY_FIRST_ON_PATH = f'PATH="{RUNTIME_OVERLAY_BIN}:$PATH"; '

#: What a process running the image's own copy of this package reports as its
#: runtime version.
RUNTIME_FLOOR = "floor"

#: The file an installed overlay version carries once complete. Kept equal to
#: `runtime_install.STAMP_NAME`, which cannot be imported from here: the
#: installer is uploaded on its own and ships in neither the overlay nor the
#: floor. `test_sandbox_command.py` holds the two together.
_OVERLAY_STAMP = ".stamp"


def running_runtime_version(
    package_file: str | None = None, *, overlay_root: str = RUNTIME_OVERLAY_ROOT
) -> str:
    """Which copy of this package the calling process imported.

    The overlay version's own stamp when the package came from the overlay,
    otherwise `RUNTIME_FLOOR`. Resolved through `current` to the version
    directory it named at the time, so asking once at import gives the
    version the process is running, not whichever one `current` names later.
    """
    if package_file is None:
        import sandbox_runtime

        package_file = sandbox_runtime.__file__ or ""
    root = Path(overlay_root).resolve()
    package = Path(package_file).resolve()
    # <root>/<version>/site-packages/sandbox_runtime/__init__.py
    if not package.is_relative_to(root) or len(package.parents) < 4:
        return RUNTIME_FLOOR
    version_directory = package.parents[2]
    if version_directory.parent != root:
        return RUNTIME_FLOOR
    try:
        stamp = (version_directory / _OVERLAY_STAMP).read_text(encoding="utf-8")
    except OSError:
        return RUNTIME_FLOOR
    return stamp.strip() or RUNTIME_FLOOR


#: Where the image bakes the same commands. The floor: a sandbox the backend
#: has not reached with an overlay yet still has every one of them here.
IMAGE_BIN = "/usr/local/bin"


def sandbox_command(name: str, *, overlay_bin: str = RUNTIME_OVERLAY_BIN) -> str:
    """The absolute path of a Lemma command: the overlay's copy, else the image's.

    For a process that cannot trust its ``PATH`` to put the Lemma copy first --
    the relay's is the image's service environment, not an agent shell's.
    Resolved per call rather than once, because the overlay can arrive after
    the process started.
    """
    overlay = f"{overlay_bin}/{name}"
    return overlay if os.access(overlay, os.X_OK) else f"{IMAGE_BIN}/{name}"


__all__ = [
    "BROWSER_PROFILE",
    "BROWSER_PROFILE_ROOT",
    "HOME_ROOT",
    "IMAGE_BIN",
    "OVERLAY_FIRST_ON_PATH",
    "RUNTIME_OVERLAY_BIN",
    "RUNTIME_FLOOR",
    "RUNTIME_OVERLAY_ROOT",
    "RUNTIME_FILESYSTEM_ROOTS",
    "WORKSPACE_ROOT",
    "is_browser_private",
    "is_inside_home",
    "running_runtime_version",
    "sandbox_command",
]
