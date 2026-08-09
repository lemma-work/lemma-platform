"""Public email providers, and the work domain an organization may claim.

This module is the authority. The frontend keeps its own copy of the same list
to name a workspace and pre-select a join policy before it talks to the server,
but that copy is only a hint: every domain claim is re-checked here. Keeping the
authority server-side is what stops a shorter client list from letting one user
claim a consumer provider — and with it every other user of that provider.
"""

from __future__ import annotations

# Providers whose addresses identify a person, never an organization. An org
# claiming one of these would auto-join strangers who happen to share a mail
# host, so ``EMAIL_DOMAIN`` is refused for all of them.
PUBLIC_EMAIL_PROVIDER_DOMAINS = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "gmx.com",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "rocketmail.com",
        "tutanota.com",
        "yahoo.com",
        "yandex.com",
        "ymail.com",
        "zoho.com",
    }
)


def normalize_email_domain(value: str) -> str:
    """Reduce a user-supplied domain to its bare, comparable form."""
    normalized = value.strip().lower().lstrip("@")
    if "://" in normalized:
        normalized = normalized.split("://", 1)[1]
    return normalized.split("/", 1)[0]


def email_domain(email: str) -> str:
    return normalize_email_domain(str(email).rsplit("@", 1)[-1])


def is_public_email_provider(domain: str) -> bool:
    return normalize_email_domain(domain) in PUBLIC_EMAIL_PROVIDER_DOMAINS


def work_domain_from_email(email: str) -> str | None:
    """The domain an organization may claim, or ``None`` for a personal address."""
    domain = email_domain(email)
    if not domain or is_public_email_provider(domain):
        return None
    return domain
