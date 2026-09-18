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

The rule is **same site**: keep a cookie if the browser would send it to the
login's origin (RFC 6265 domain-matching), or if it belongs to another host of
the same registrable domain.

That second half was missing, and it broke the feature on the first real site
it met. A login is not one host. Lemma's own deployment serves its app on
`asur.work` and its API on `api.asur.work`, and SuperTokens' session cookies --
`sAccessToken` and `sRefreshToken`, the HttpOnly pair that *is* the session --
are set by the API host. Domain-matching alone kept only what the website host
had set: `sFrontToken` and a timestamp, the two cookies the frontend SDK reads
to decide a session exists. Restoring that produced a browser that believed it
was signed in, got a 401, and bounced to the login form -- so the agent asked
again, and again, each time saving the same useless pair.

The registrable domain is the web's own boundary for this, and it has to be a
real public-suffix list rather than "the last two labels": `a.github.io` and
`b.github.io` are different sites and must not share a login, while
`app.example.co.uk` and `api.example.co.uk` are one. The private section of the
list is what draws that first line, so it is switched on. A host with no
registrable domain at all -- `localhost`, a bare IP -- falls back to exact
matching, which is the only safe reading of "the same site" there.

What this still drops is the thing worth dropping: a login that redirected
through `accounts.google.com` leaves Google's cookies in the capture, and they
are not this site's to keep.

The first implementation of this feature kept the whole browser profile under
one site's name -- every site the agent had ever visited, restored whenever any
agent asked for that one. That is what this exists to make impossible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from tldextract import TLDExtract

from app.modules.workspace.contracts.browser import (
    BrowserCookie,
    BrowserOrigin,
    BrowserState,
    host_of,
)

#: Cookie and storage entries are bounded so one site cannot make a saved login
#: into a row nothing can read back. A policy number, which is why it lives
#: here rather than with the shape.
#: Stands in for "never used" when ordering candidates.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

MAX_COOKIES = 200
MAX_ORIGINS = 20

#: The public-suffix list, from the copy shipped inside `tldextract`.
#:
#: `suffix_list_urls=()` and `cache_dir=None` between them make this offline
#: and deterministic: no fetch on first use, no cache directory to write, the
#: same answer in a test, in a worker and in CI. The cost is that the snapshot
#: ages with the dependency, which for this decision is the right trade -- a
#: scoping rule that reaches the network is a scoping rule that can fail open.
#:
#: `include_psl_private_domains=True` is load-bearing, not a default worth
#: leaving alone. Without it `github.io` is not a suffix, so `a.github.io` and
#: `b.github.io` resolve to the same registrable domain and a login saved for
#: one would restore the other's cookies.
_registrable = TLDExtract(
    suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None
)


def site_of(host: str) -> str:
    """The registrable domain `host` belongs to, or `""` if it has none.

    Empty for `localhost`, for a bare IP address, and for a public suffix on
    its own -- none of which have a "rest of the site" to speak of.
    """
    if not host:
        return ""
    return _registrable(host.lower()).top_domain_under_public_suffix


def same_site(cookie_domain: str, host: str) -> bool:
    """Whether a cookie's host and the login's host are one site.

    Not the same question as `domain_matches`, and both are needed: that one
    answers "would the browser send this cookie to the login's origin", which
    covers a parent-domain cookie; this one covers a sibling, which is where
    an API host's session cookies live.
    """
    candidate = (cookie_domain or "").lstrip(".").lower()
    subject = (host or "").lower()
    if not candidate or not subject:
        return False
    site = site_of(subject)
    # No registrable domain means there is no site to be part of, so the only
    # honest answer is the exact host -- `localhost` must not pull in cookies
    # from every other single-label name.
    if not site:
        return candidate == subject
    return site_of(candidate) == site


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


def pick_for_site(origin: str, candidates):
    """Which of a person's saved logins authenticates `origin`.

    Exact origin first, so a site that really does keep a separate login per
    host gets its own. Otherwise the most recently used login of the same
    registrable domain: a session captured at `mail.google.com` is one the
    browser also sends to `calendar.google.com`, and `scope_state` keeps it
    for that reason -- looking it back up by exact origin threw that away and
    asked the person to sign in to an account they were already signed in to.

    `None` when nothing fits, including when `origin` has no registrable
    domain: `localhost` and bare IPs have no rest-of-the-site to borrow from,
    and guessing would share one login between unrelated hosts.

    Pure, and separate from the query that feeds it, because this is the part
    worth being sure about -- a fake session cannot tell two queries apart and
    a test against one would agree with whatever it was given.
    """
    wanted = normalized_origin_key(origin)
    for candidate in candidates:
        if normalized_origin_key(candidate.origin) == wanted:
            return candidate

    site = site_of(host_of(origin))
    if not site:
        return None
    same_site = [c for c in candidates if site_of(host_of(c.origin)) == site]
    if not same_site:
        return None
    # `last_used_at` is optional, and `None` sorts before any timestamp rather
    # than blowing up the comparison.
    return max(
        same_site,
        key=lambda c: (
            c.last_used_at is not None,
            c.last_used_at or _EPOCH,
            c.origin,
        ),
    )


def normalized_origin_key(origin: str) -> str:
    """An origin reduced to what "the same origin" means here."""
    return (origin or "").strip().rstrip("/").lower()


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
        and (
            domain_matches(str(cookie.get("domain", "")), host)
            or same_site(str(cookie.get("domain", "")), host)
        )
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
    "same_site",
    "site_of",
    "looks_signed_in",
    "pick_for_site",
    "scope_state",
]


#: Words a page shows when it still wants a login. Crude on purpose: the
#: alternative is asking a model, and a wrong answer here either asks a person
#: who did not need asking or reports a sign-in that did not happen.
_WALL_HINTS = ("sign in", "signin", "log in", "login", "password")


def page_looks_like_a_login_wall(text: str) -> bool:
    """Whether a page still appears to want a login.

    Used after loading a saved session: if the site shows a login form anyway,
    the session is dead and saying so now is what `PS-CONN-022` asks for.

    Here rather than beside the service that calls it, because this is the
    same question `looks_signed_in` asks from the other side -- and because a
    service file is not the place for a word list.
    """
    lowered = (text or "").lower()[:4000]
    return any(hint in lowered for hint in _WALL_HINTS)
