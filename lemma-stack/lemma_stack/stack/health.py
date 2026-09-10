"""Health gates used by the start sequence."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from lemma_stack.output import AdminError
from lemma_stack.runtime.base import Runtime


def wait_container_healthy(runtime: Runtime, name: str, *, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runtime.container_health(name)
        if status == "healthy":
            return
        if not runtime.container_running(name):
            raise AdminError(exit_reason(runtime, name))
        time.sleep(2)
    raise AdminError(f"{name} did not become healthy within {int(timeout)}s")


# What PostgreSQL says when its data directory was written by another major.
# It refuses to start rather than touching the files, which is the right
# behaviour and also the reason nothing else notices: the container simply
# exits, and the start sequence reported only that it had.
_PG_INCOMPATIBLE = "database files are incompatible with server"


def exit_reason(runtime: Runtime, name: str) -> str:
    """Say why a container exited, when its own logs already explain it.

    "lemma-local-db exited while waiting for it to become healthy" is true of a
    corrupt volume, a bad password, a port collision and an out-of-memory kill
    alike. It is also what a PostgreSQL major upgrade looks like — the one cause
    here with a known remedy and no way to guess it, since the data is intact
    and the fix is to remove the volume deliberately rather than to retry.
    """
    generic = f"{name} exited while waiting for it to become healthy"
    try:
        logs = runtime.run("logs", "--tail", "80", name, check=False)
    except OSError:
        return generic
    output = f"{logs.stdout or ''}{logs.stderr or ''}"
    if _PG_INCOMPATIBLE not in output:
        return generic

    # PostgreSQL names both versions in the line after the refusal; quote its
    # own words rather than paraphrasing a version we did not read.
    detail = next(
        (
            line.strip()
            for line in output.splitlines()
            if "initialized by PostgreSQL version" in line
        ),
        "",
    )
    return (
        f"{name} will not start: its stored data was written by a different "
        f"PostgreSQL major version. "
        + (f"PostgreSQL reports: {detail} " if detail else "")
        + "Lemma does not upgrade a Postgres data directory in place, so this "
        "needs the volume removed deliberately -- `lemma down` takes a flag "
        "that deletes it, which erases the pods and files stored in it. Back "
        "anything up you need first."
    )


def wait_http(url: str, *, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            # the server answered; auth/404-style responses still mean "up"
            if exc.code < 500:
                return
            last_error = exc
        except OSError as exc:
            last_error = exc
        time.sleep(2)
    detail = f" (last error: {last_error})" if last_error else ""
    raise AdminError(f"{url} did not respond within {int(timeout)}s{detail}")
