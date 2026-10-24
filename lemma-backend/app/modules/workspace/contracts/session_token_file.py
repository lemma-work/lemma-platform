"""Where one session's delegated token is written inside the sandbox.

A person has one sandbox, and every session of every agent of theirs runs in
it. What must differ between them -- today the delegated ``LEMMA_TOKEN`` -- is
delivered per session, never written anywhere the next session would also
read. The token travels in the session's environment, and again in a file the
environment names (``LEMMA_TOKEN_FILE``), because an environment cannot be
rewritten under a process that is already running and a file can.

That keeps one session from *using* another's credential by accident. It does
not keep one from *reading* another's: every process in the sandbox runs as the
same UID, so a session that goes looking finds the other directories and the
other processes' environments. A Linux user per actor is the step that would
close that, and it is not taken yet; what is taken is that a lifted token
counts for minutes, and not at all once its conversation has ended.

Under ``/tmp``, so a credential never reaches the durable disk: one directory
per session under ``SESSION_ROOT``, where any other per-session credential
belongs beside it.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re

from sandbox_runtime.paths import SESSION_ROOT

# The runtime's own names, so the path the backend sends is the one shape the
# runtime agrees to write (``sandbox_runtime.workspace.session_token``).
from sandbox_runtime.workspace.session_token import (
    TOKEN_FILE_NAME,
    TOKEN_FILE_VARIABLE,
)

_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_UNSAFE_CHARACTER = re.compile(r"[^A-Za-z0-9_-]")


def session_token_dir(session_id: str) -> str:
    """The session's own directory: its id when that is a safe path segment.

    Anything else -- a slash, a dot, a very long id -- is reduced to a safe
    prefix and suffixed with a digest of the whole id, so no id climbs out of
    the root and two ids never share a directory.
    """
    if _SAFE_SESSION_ID.fullmatch(session_id):
        segment = session_id
    else:
        digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
        segment = f"{_UNSAFE_CHARACTER.sub('_', session_id)[:40]}-{digest}"
    return f"{SESSION_ROOT}/{segment}"


def session_token_path(session_id: str) -> str:
    """The file this session's delegated token is written to, ``0600``."""
    return f"{session_token_dir(session_id)}/{TOKEN_FILE_NAME}"


#: A shell prefix that writes ``$LEMMA_TOKEN`` to ``$LEMMA_TOKEN_FILE`` before
#: the command after it, for a fabric with no runtime of ours to do it (E2B).
#: Through a staging file and a rename, so a process reading the file never
#: sees half a token; with absolute paths to the tools, so a ``PATH`` the home
#: leads plays no part; and silent, because a token that could not be written
#: still reaches the command through its environment.
TOKEN_FILE_SHELL_PREFIX = (
    'if [ -n "${LEMMA_TOKEN_FILE:-}" ] && [ -n "${LEMMA_TOKEN:-}" ]; then ('
    "umask 077; "
    'case "$LEMMA_TOKEN_FILE" in *..*) exit 0;; '
    f"{SESSION_ROOT}/*/{TOKEN_FILE_NAME}) ;; *) exit 0;; esac; "
    'd="${LEMMA_TOKEN_FILE%/*}"; '
    '/bin/mkdir -p "$d" && /bin/chmod 700 "$d" && '
    'printf %s "$LEMMA_TOKEN" > "$LEMMA_TOKEN_FILE.$$" && '
    '/bin/mv -f "$LEMMA_TOKEN_FILE.$$" "$LEMMA_TOKEN_FILE"'
    ") 2>/dev/null; fi; "
)


def with_token_file(command: str, environment: Mapping[str, str]) -> str:
    """``command``, writing the session's token file first when it has one."""
    if environment.get("LEMMA_TOKEN") and environment.get(TOKEN_FILE_VARIABLE):
        return TOKEN_FILE_SHELL_PREFIX + command
    return command


__all__ = [
    "SESSION_ROOT",
    "TOKEN_FILE_NAME",
    "TOKEN_FILE_SHELL_PREFIX",
    "TOKEN_FILE_VARIABLE",
    "session_token_dir",
    "session_token_path",
    "with_token_file",
]
