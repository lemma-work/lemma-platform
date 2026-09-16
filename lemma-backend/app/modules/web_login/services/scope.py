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

from app.modules.workspace.contracts.browser import (
    BrowserCookie,
    BrowserOrigin,
    BrowserState,
    host_of,
)

#: Cookie and storage entries are bounded so one site cannot make a saved login
#: into a row nothing can read back. A policy number, which is why it lives
#: here rather than with the shape.
MAX_COOKIES = 200
MAX_ORIGINS = 20


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


def scope_state(
    state: BrowserState | dict[str, object], *, origin: str
) -> BrowserState:
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


#: Cookie names that a site sets before anybody has signed in to anything.
#:
#: Not a security control and not trying to be: a site determined to look
#: signed-in could name a cookie whatever it likes, and nothing here would be
#: worse off than the check that came before, which was "is there any cookie at
#: all". What this removes is the ordinary case that made that check useless --
#: a consent banner sets one of these on the first page view, so pressing "I'm
#: signed in" on a login page nobody had filled in captured a cookie, passed,
#: and told the person their login was kept.
_NOT_A_SESSION = (
    "cookieconsent",
    "cookie_consent",
    "cookielawinfo",
    "consent",
    "gdpr",
    "euconsent",
    "csrf",
    "xsrf",
    "_ga",
    "_gid",
    "_gcl_au",
    "_fbp",
    "locale",
    "lang",
    "timezone",
    "theme",
)


def _might_carry_a_session(cookie: object) -> bool:
    """Whether one cookie could plausibly be what keeps somebody signed in."""
    name = str(getattr(cookie, "name", "") or (cookie or {}).get("name", "")).lower()  # type: ignore[union-attr]
    if not name:
        return False
    return not any(marker in name for marker in _NOT_A_SESSION)


def looks_signed_in(state: BrowserState | dict[str, object], *, origin: str) -> bool:
    """Whether a scoped capture carries something that could be a session.

    A person who pressed "I'm signed in" on a page they had not signed in to
    leaves a capture with nothing in it worth keeping. Saving it would mean the
    next run loads nothing, finds a login wall, and asks again -- with the
    person told it had been saved. Better to say so at the moment they can
    still do something about it.

    "Anything at all" was the first test and it does not survive contact with
    the web: a cookie banner sets a cookie before the login form is even
    filled in, so the capture was never empty and the check never fired. Local
    storage for the origin still counts on its own -- a token kept there is a
    real way to be signed in.
    """
    scoped = scope_state(state, origin=origin)
    if scoped["origins"]:
        return True
    return any(_might_carry_a_session(cookie) for cookie in scoped["cookies"])


def _storage_key(origin: str) -> str:
    """An origin reduced to what local storage is actually keyed by."""
    parsed = urlparse(origin if "://" in origin else f"https://{origin}")
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    scheme = parsed.scheme or "https"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


__all__ = [
    "MAX_COOKIES",
    "BrowserCookie",
    "BrowserOrigin",
    "BrowserState",
    "MAX_ORIGINS",
    "domain_matches",
    "host_of",
    "looks_signed_in",
    "scope_state",
]
