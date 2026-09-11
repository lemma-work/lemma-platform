"""Keeping only what belongs to the site somebody signed in to.

A browser's stored state is cookies plus per-origin local storage. Both arrive
from the sandbox as whatever that browser session held, and this narrows them to
the one site the login is for before anything is encrypted and kept.

There are two layers, and it matters that they are independent:

1. **Where the sign-in happened.** Each login gets its own browser session named
   for its site, so a capture taken from it contains that site because that is
   the only place it has been. Scoping by construction.
2. **This filter.** Belt to that brace, and the one that survives somebody
   later reusing a session for two sites, or a page setting a cookie for a
   parent domain.

The rule is the browser's own: keep a cookie if the browser would send it to the
login's origin. That is RFC 6265 domain-matching, and it is deliberately *not*
"same registrable domain" -- a public-suffix list would answer a slightly
different question, and the question worth answering is what the site actually
receives. A cookie for `.example.com` is kept for a login at
`accounts.example.com`, because the browser sends it there; a cookie for
`other.example.com` is not, because it does not.

The first implementation of this feature kept the whole browser profile under
one site's name -- every site the agent had ever visited, restored whenever any
agent asked for that one. That is what this exists to make impossible.
"""

from __future__ import annotations

from urllib.parse import urlparse

#: Cookie and storage entries are bounded so one site cannot make a saved login
#: into a row nothing can read back.
MAX_COOKIES = 200
MAX_ORIGINS = 20


def host_of(origin: str) -> str:
    """The bare host of a normalized origin, without any port."""
    parsed = urlparse(origin if "://" in origin else f"https://{origin}")
    return (parsed.hostname or "").lower()


def domain_matches(cookie_domain: str, host: str) -> bool:
    """Whether a browser would send this cookie to `host`.

    RFC 6265 §5.1.3: a cookie domain matches either exactly, or when the host
    is a subdomain of it. The leading dot browsers historically wrote is not
    meaningful and is stripped before comparing.

    A single-label domain (`com`, `localhost`) matches only itself. Browsers
    refuse to set cookies on a public suffix, so one cannot arrive here from a
    real capture -- but a filter that trusted that, and was one day handed a
    hand-built state, would return every cookie in it.
    """
    candidate = (cookie_domain or "").lstrip(".").lower()
    subject = (host or "").lower()
    if not candidate or not subject:
        return False
    if candidate == subject:
        return True
    if "." not in candidate:
        return False
    return subject.endswith(f".{candidate}")


def scope_state(state: dict, *, origin: str) -> dict:
    """A captured browser state, narrowed to one site.

    Returns the same shape the browser gave, so it can be handed straight back
    on injection. Everything that is not a cookie the site would receive, or
    storage for exactly that origin, is dropped rather than kept and ignored:
    what is not stored cannot later be restored somewhere it does not belong.
    """
    host = host_of(origin)
    if not host:
        return {"cookies": [], "origins": []}

    cookies = [
        cookie
        for cookie in _as_list(state.get("cookies"))[: MAX_COOKIES * 5]
        if isinstance(cookie, dict)
        and domain_matches(str(cookie.get("domain", "")), host)
    ][:MAX_COOKIES]

    # Local storage is keyed by the exact origin that wrote it -- there is no
    # subdomain rule for it in the browser, so there is none here.
    wanted = _storage_key(origin)
    origins = [
        entry
        for entry in _as_list(state.get("origins"))[: MAX_ORIGINS * 5]
        if isinstance(entry, dict)
        and _storage_key(str(entry.get("origin", ""))) == wanted
    ][:MAX_ORIGINS]

    return {"cookies": cookies, "origins": origins}


def looks_signed_in(state: dict, *, origin: str) -> bool:
    """Whether a scoped capture actually carries anything for this site.

    A person who pressed "I'm signed in" on a page they had not signed in to
    leaves an empty capture. Saving it would mean the next run loads nothing,
    finds a login wall, and asks again -- with the person told it had been
    saved. Better to say so at the moment they can still do something about it.
    """
    scoped = scope_state(state, origin=origin)
    return bool(scoped["cookies"] or scoped["origins"])


def _storage_key(origin: str) -> str:
    """An origin reduced to what local storage is actually keyed by."""
    parsed = urlparse(origin if "://" in origin else f"https://{origin}")
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    scheme = parsed.scheme or "https"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


__all__ = [
    "MAX_COOKIES",
    "MAX_ORIGINS",
    "domain_matches",
    "host_of",
    "looks_signed_in",
    "scope_state",
]
