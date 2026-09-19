"""Which sites somebody actually told us they signed in to.

Measured before it was written: you cannot tell a login from a tracking
cookie by reading the cookie store. On a real profile, `api.asur.work` held
two HttpOnly session cookies -- a genuine login -- and `youtube.com` held
six HttpOnly cookies belonging to nobody, set by visiting one video. Every
flag CDP reports (`httpOnly`, `secure`, `session`, `sameSite`) says the same
thing about both. Sorting them by cookie *name* is exactly the guess that
`looks_signed_in` made, and it was wrong in production in three ways.

So this does not guess. When a person answers "yes, I signed in" to a
`browser_sign_in` request, that is a fact, freely given, about one site --
and the old design threw it away. It is written here instead.

**Names only.** A registrable domain and when it was marked. No cookie, no
value, no token: a list of sites, which is the same thing the list endpoint
already returns and is worth nothing to anybody who reads it.

**Beside the profile, not inside it.** `/home/user/.lemma/browser/` is
durable on both fabrics and quiesce only removes the four lock files inside
`profile/`, so a mark outlives a suspend exactly as the cookie it describes
does. A profile wiped by hand leaves marks behind, and that is harmless: the
reader intersects them with cookies the browser actually holds, so a mark
with nothing behind it simply never appears.

Not a table, deliberately. A fact about one browser profile belongs with
that profile, lives and dies with it, and needs no migration when it changes.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from sandbox_runtime.paths import BROWSER_PROFILE_ROOT

#: Beside `profile/`, for the reason in the module docstring.
MARKS_FILE = Path(BROWSER_PROFILE_ROOT) / "signed-in.json"

#: An object rather than a bare list, so a later field does not break the
#: format for a profile written by an older image.
_EMPTY: dict[str, Any] = {"sites": []}


def _read() -> dict[str, Any]:
    """The marks, or an empty set if there are none or they are unreadable.

    A corrupt file reads as empty rather than raising. The worst case is that
    a site stops being labelled "signed in" while its cookies stay exactly
    where they are -- and the alternative, failing the whole listing because
    a cache of labels is malformed, is worse than the label being missing.
    """
    try:
        loaded = json.loads(MARKS_FILE.read_text())
    except OSError, ValueError:
        return dict(_EMPTY)
    if not isinstance(loaded, dict):
        return dict(_EMPTY)
    sites = loaded.get("sites")
    if not isinstance(sites, list):
        return dict(_EMPTY)
    return {"sites": [str(site) for site in sites if isinstance(site, str)]}


def _write(payload: dict[str, Any]) -> None:
    """Replace the file in one step.

    Written to a neighbour and renamed: `rename` within a directory is
    atomic, so a reader during a write sees the old file or the new one and
    never a half-written one. The same reason Chrome does it for the
    preferences beside this.
    """
    MARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=str(MARKS_FILE.parent),
        prefix=".signed-in-",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle as out:
            json.dump(payload, out)
        Path(handle.name).replace(MARKS_FILE)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def signed_in_sites() -> list[str]:
    """Every site somebody has said they signed in to, lowercased and sorted."""
    return sorted({site.lower() for site in _read()["sites"] if site})


def mark_signed_in(site: str) -> list[str]:
    """Record one site. Idempotent -- answering twice is one sign-in."""
    wanted = site.strip().lstrip(".").lower()
    if not wanted:
        return signed_in_sites()
    current = set(signed_in_sites())
    if wanted in current:
        return sorted(current)
    updated = sorted(current | {wanted})
    _write({"sites": updated})
    return updated


def forget_marks(sites: list[str]) -> list[str]:
    """Drop these sites' marks, for a sign-out.

    Separate from dropping the cookies, and called beside it rather than by
    it: `cookies.py` is given hosts and knows nothing about which of them are
    one site, which is the whole reason the public-suffix split exists.
    """
    unwanted = {site.strip().lstrip(".").lower() for site in sites if site}
    if not unwanted:
        return signed_in_sites()
    current = set(signed_in_sites())
    remaining = current - unwanted
    if remaining == current:
        return sorted(current)
    _write({"sites": sorted(remaining)})
    return sorted(remaining)


__all__ = ["MARKS_FILE", "forget_marks", "mark_signed_in", "signed_in_sites"]
