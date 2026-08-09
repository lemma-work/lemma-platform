"""Deciding whether a URL belongs to a domain we trust.

The tempting version of this check is a substring test, and it is wrong in a
way that reads as correct: ``"sharepoint.com" in host`` accepts
``sharepoint.com.attacker.test``, and ``host.endswith("duckduckgo.com")``
accepts ``evil-duckduckgo.com``. Both hand an attacker-chosen host whatever the
check was gating -- a credential, or a URL we then treat as the provider's.

Matching on the label boundary is the whole content of this module; it lives
here so the rule has one implementation rather than one per caller.
"""

from __future__ import annotations

from urllib.parse import urlparse


def hostname_of(url: str) -> str:
    """The lowercased host of ``url``, without port or userinfo."""
    return (urlparse(url).hostname or "").lower()


def host_is_within(hostname: str, domain: str) -> bool:
    """True for ``domain`` itself or a real subdomain of it."""
    return hostname == domain or hostname.endswith(f".{domain}")


def url_is_within(url: str, domain: str) -> bool:
    """True when ``url``'s host is ``domain`` or a real subdomain of it."""
    return host_is_within(hostname_of(url), domain)
