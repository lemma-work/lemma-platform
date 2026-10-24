"""A session's delegated token, written where its environment says to look.

The backend gives every workspace session a token that lives minutes, in
``LEMMA_TOKEN``, and names a file for it in ``LEMMA_TOKEN_FILE``; the Lemma SDK
reads the file first. A process's environment is fixed when it starts and a
file is not, so the file is how a fresh token reaches a process that is already
running -- a server, a long loop -- and it is rewritten here with whatever token
each new command or Python call arrives with.

Only a path of exactly one shape is written: a file named ``lemma-token`` in a
directory of its own directly under ``SESSION_ROOT``. The directory is made
``0700`` and the file ``0600``, created with its mode and moved into place, so
a reader never sees half a token. Those modes are a boundary only between Linux
users, and every session runs as the same one today.

Best effort: a token that could not be written still reaches the command in its
environment, and the SDK takes the fresher of the two.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import PurePosixPath

from sandbox_runtime.paths import SESSION_ROOT

TOKEN_VARIABLE = "LEMMA_TOKEN"
TOKEN_FILE_VARIABLE = "LEMMA_TOKEN_FILE"
TOKEN_FILE_NAME = "lemma-token"


def _session_token_path(raw: str) -> PurePosixPath | None:
    path = PurePosixPath(raw)
    if (
        path.name != TOKEN_FILE_NAME
        or path.parent.parent != PurePosixPath(SESSION_ROOT)
        or ".." in path.parts
    ):
        return None
    return path


def deliver_token_file(environment: Mapping[str, str]) -> bool:
    """Write ``environment``'s token to the file it names. True when written."""
    token = environment.get(TOKEN_VARIABLE)
    raw = environment.get(TOKEN_FILE_VARIABLE)
    path = _session_token_path(raw) if raw else None
    if not token or path is None:
        return False
    directory = str(path.parent)
    staged = f"{path}.{os.getpid()}"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        try:
            os.unlink(staged)
        except FileNotFoundError:
            pass
        descriptor = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.replace(staged, path)
    except OSError:
        try:
            os.unlink(staged)
        except OSError:
            pass
        return False
    return True


__all__ = ["deliver_token_file"]
