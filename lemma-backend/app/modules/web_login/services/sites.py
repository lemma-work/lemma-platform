"""What counts as one site, and what a login wall looks like.

The small, true part of what used to be `scope.py`. That file existed to
narrow a *captured* browser state down to one site before encrypting and
storing it -- deciding which cookies were the login, which were a consent
banner, and which belonged to an identity provider passed through on the way.
The browser keeps its own profile now, so there is nothing to narrow and
nothing to decide; the guessing went with it.

What survives is the two questions that were never guesses:

* **which site is this**, so the sites a person is signed in to can be listed
  and forgotten one at a time rather than host by host; and
* **does this page still want a login**, which is how a sign-in knows whether
  it is already done before asking anybody.
"""

from __future__ import annotations

from tldextract import TLDExtract

#: Offline and deterministic. `suffix_list_urls=()` because a rule that
#: reaches the network is a rule that can fail open, and `cache_dir=None`
#: because a cache is a second place for it to differ.
#:
#: `include_psl_private_domains=True` is load-bearing rather than a default
#: left alone: without it `github.io` is not a suffix, so `a.github.io` and
#: `b.github.io` are one registrable domain -- and forgetting the login for
#: one would clear the other's cookies.
_registrable = TLDExtract(
    suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None
)


def site_of(host: str) -> str:
    """The registrable domain `host` belongs to, or `""` if it has none.

    Empty for `localhost`, for a bare IP address, and for a public suffix on
    its own -- none of which have a "rest of the site" to speak of.

    This is what groups a browser's cookies into the sites a person recognises.
    `asur.work` and `api.asur.work` are one login to them, and listing those
    as two entries -- one of which is the half they never visited -- is not a
    list anybody can act on.
    """
    if not host:
        return ""
    return _registrable(host.lower()).top_domain_under_public_suffix


def same_site(host: str, other: str) -> bool:
    """Whether two hosts are the same site.

    Falls back to an exact match where there is no registrable domain, which
    is the only honest reading for `localhost` or a bare IP: those must not
    pull in every other single-label name.
    """
    left = (host or "").lstrip(".").lower()
    right = (other or "").lower()
    if not left or not right:
        return False
    site = site_of(right)
    if not site:
        return left == right
    return site_of(left) == site


#: Words a page shows when it still wants a login. Not a security control and
#: not trying to be -- a site can word its form however it likes. It is a
#: shortcut past asking a person who does not need asking.
_WALL_HINTS = ("sign in", "signin", "log in", "login", "password")


def page_looks_like_a_login_wall(text: str) -> bool:
    """Whether a page still appears to want a login.

    Read from where the browser *landed* -- its address and title -- after
    being steered to the site. That is a far better signal than it used to
    get: the previous design asked this of a page it had loaded a restored
    session into, and answered by inspecting cookie names, which is how a
    consent banner came to be read as a session.

    Still a heuristic, and the failure mode is worth naming: a site that
    serves its login form at the same address under a neutral title passes
    this. That costs one unnecessary ask, which is the right way round.
    """
    lowered = (text or "").lower()[:4000]
    return any(hint in lowered for hint in _WALL_HINTS)


__all__ = ["page_looks_like_a_login_wall", "same_site", "site_of"]


def site_from_origin(origin: str) -> str:
    """The site an origin belongs to, as the cookie list names it.

    An origin carries a scheme and often a port; a cookie's domain carries
    neither. Both have to reduce to the same string or forgetting a site
    matches none of its cookies -- which is exactly what happened for
    `http://127.0.0.1:18099`: `site_of` is empty for a bare IP, the caller
    fell back to the whole origin, and `same_site("127.0.0.1", "http://
    127.0.0.1:18099")` is false. The delete reported "nothing to forget"
    about a site the browser was plainly signed in to.

    The port goes because a cookie is not scoped by port -- one is set for a
    host and every port on it sees it -- so `:18099` names nothing a person
    could forget separately.
    """
    host = origin.split("://", 1)[-1].split("/", 1)[0].strip().lower()
    if host.startswith("["):
        # IPv6 literal: the brackets hold the colons that are part of the
        # address rather than a port separator.
        host = host.partition("]")[0].lstrip("[")
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return site_of(host) or host
