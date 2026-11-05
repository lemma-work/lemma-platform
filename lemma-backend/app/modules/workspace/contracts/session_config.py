"""Where one session's own tool configuration lives inside the sandbox.

A person has one sandbox, and every session of every agent of theirs runs in
it. What must differ between them -- the delegated ``LEMMA_TOKEN``, and the
GitHub credential `git` and `gh` use -- is therefore delivered per session,
never written anywhere the next session would also read. The token travels in
the session's environment; the GitHub credential is a file, so the session's
environment says which file.

That keeps one session from *using* another's credential by accident: two
conversations writing `git config --global` raced, and the last writer's
account was the one every later push went out as. It does not keep one from
*reading* another's: every process in the sandbox runs as the same UID, so a
session that goes looking finds the other directories and the other
processes' environments. A Linux user per actor is the step that would close
that, and it is not taken yet.

Under ``/tmp``, so a credential never reaches the durable disk.
"""

from __future__ import annotations

import hashlib
import re

#: The parent of every session's directory.
SESSION_CONFIG_ROOT = "/tmp/lemma-sess"

_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_UNSAFE_CHARACTER = re.compile(r"[^A-Za-z0-9_-]")


def session_config_dir(session_id: str) -> str:
    """This session's directory: its id when that is a safe path segment.

    Anything else -- a slash, a dot, a very long id -- is reduced to a safe
    prefix and suffixed with a digest of the whole id, so no id climbs out of
    the root and two ids never share a directory.
    """
    if _SAFE_SESSION_ID.fullmatch(session_id):
        segment = session_id
    else:
        digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
        segment = f"{_UNSAFE_CHARACTER.sub('_', session_id)[:40]}-{digest}"
    return f"{SESSION_CONFIG_ROOT}/{segment}"


def session_gitconfig_path(session_id: str) -> str:
    """The git config file this session's `git` reads after every other."""
    return f"{session_config_dir(session_id)}/gitconfig"


def session_git_credentials_path(session_id: str) -> str:
    return f"{session_config_dir(session_id)}/git-credentials"


def session_gh_config_dir(session_id: str) -> str:
    return f"{session_config_dir(session_id)}/gh"


def session_config_env(session_id: str) -> dict[str, str]:
    """What a session's environment carries so its tools read its own files.

    `git` is pointed at its file by an include in the command-line scope, the
    one read last, rather than by ``GIT_CONFIG_GLOBAL``. That would stand in
    for ``~/.gitconfig`` altogether: the person's own settings would vanish
    from the session, and ``git config --global`` would fail outright until the
    file had been written. An include of a file not yet there is ignored.
    """
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "include.path",
        "GIT_CONFIG_VALUE_0": session_gitconfig_path(session_id),
        "GH_CONFIG_DIR": session_gh_config_dir(session_id),
    }


__all__ = [
    "SESSION_CONFIG_ROOT",
    "session_config_dir",
    "session_config_env",
    "session_gh_config_dir",
    "session_git_credentials_path",
    "session_gitconfig_path",
]
