"""Which upstream tenant a connected account speaks for.

Three connectors already derive this and each stores it somewhere different
inside the credential blob: Jira and Confluence resolve a ``cloud_id`` (and
build a per-tenant base URL from it), Teams decodes ``tid`` out of the access
token's claims, Slack's token response carries ``team.id``. Surfaces then dig
the same values back out of that blob by hand to fill its own
``external_workspace_id`` / ``external_tenant_id`` columns.

It is one concept -- *the upstream tenant this account speaks for* -- discovered
three times and named three ways. Declaring the paths in one table makes it one
concept again, and gives inbound delivery something it can actually query:
``credentials`` is encrypted JSONB, so nothing inside it can be indexed or
matched against a webhook payload.

It hangs off the **account**, not the organization's install, and that is not an
accident. One Slack app can be installed in many workspaces, and one GitHub App
in many organizations, all under a single Lemma auth config -- so the tenant is
whatever the individual authorization was for. An install-level column would be
correct only until the second workspace connected.

Deriving the values stays where it has to (a network call for Atlassian, a JWT
decode for Teams). Only the *extraction* is declared here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

# Where each connector's upstream tenant id lands in the stored credentials,
# most specific first. A connector absent from this table has no tenant of its
# own, which is the common case: most providers issue one token for one place.
#
# This is deliberately *not* the same thing as `provider_account_id`, which is
# the human's handle on the provider (`login`, `authed_user.id`) and is what the
# account uniqueness index is about. A tenant is shared; a handle is not.
_TENANT_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "jira": (("user_data", "cloud_id"),),
    "confluence": (("user_data", "cloud_id"),),
    "microsoft_teams": (
        ("user_data", "tenant_id"),
        ("user_data", "tid"),
        ("raw_response", "tenant_id"),
        ("raw_response", "tid"),
    ),
    "slack": (
        ("raw_response", "team", "id"),
        ("raw_response", "team_id"),
    ),
}

# Connectors whose *callback* names the tenant outright, as a query parameter
# on the redirect back. GitHub's App install redirect carries `installation_id`,
# and it is the authority rather than a hint: the credentials that come back are
# the authorizing user's own and say nothing about which installation was just
# authorized -- the same user, in two organizations, gets two installations and
# one indistinguishable pair of tokens.
_CALLBACK_PARAMS: dict[str, tuple[str, ...]] = {
    "github": ("installation_id",),
}

# Composio owns the connection rather than us, and names it the same way for
# every toolkit, so it needs no per-connector entry.
_BROKERED_PATHS: tuple[tuple[str, ...], ...] = (("connection_id",),)

_MAX_REF_LENGTH = 255

#: A stored credential in either shape it actually arrives in: a typed
#: `OAuthCredentials` on the OAuth paths, a plain mapping on the
#: credential-managed ones. Only the mapping is walked; see
#: `resolve_external_ref`, which learned that the hard way.
CredentialBlob = Any


def _dig(source: Any, path: tuple[str, ...]) -> str | None:
    for key in path:
        if not isinstance(source, dict):
            return None
        source = source.get(key)
    if source is None or isinstance(source, (dict, list, bool)):
        return None
    text = str(source).strip()
    # The column is String(255). A silently truncated key matches nothing on the
    # way back in, which is worse than having no key at all.
    return text if text and len(text) <= _MAX_REF_LENGTH else None


def resolve_external_ref(connector_id: str, credentials: CredentialBlob) -> str | None:
    """The upstream tenant this account's events will arrive under, if any.

    Takes a credential in either shape it actually arrives in. Callers hold a
    typed `OAuthCredentials` on the OAuth paths and a plain mapping on the
    credential-managed ones, and `_dig` only walks mappings -- so while this
    declared a dict, every OAuth account silently resolved to `None` and the
    column stayed empty for all of them. A routing key that is always absent
    fails quietly, which is why it went unnoticed.
    """
    if credentials is None:
        return None
    if not isinstance(credentials, dict):
        dump = getattr(credentials, "model_dump", None)
        credentials = dump() if callable(dump) else None
    if not credentials:
        return None
    key = (connector_id or "").strip().lower()
    for path in (*_TENANT_PATHS.get(key, ()), *_BROKERED_PATHS):
        found = _dig(credentials, path)
        if found is not None:
            return found
    return None


def _from_callback(connector_id: str, callback_url: str | None) -> str | None:
    """The tenant the provider named on the way back, if it named one."""
    if not callback_url:
        return None
    names = _CALLBACK_PARAMS.get((connector_id or "").strip().lower(), ())
    if not names:
        return None
    try:
        query = parse_qs(urlsplit(callback_url).query)
    except ValueError:
        return None
    for name in names:
        for value in query.get(name, ()):
            text = (value or "").strip()
            if text and len(text) <= _MAX_REF_LENGTH:
                return text
    return None


def bind_external_ref(
    connector_id: str,
    credentials: CredentialBlob,
    callback_url: str | None = None,
) -> str | None:
    """The tenant to bind an account to, from either place it can be stated.

    A callback that names the tenant outranks one dug out of credentials. It is
    a stronger statement: the provider is telling us which tenant *this
    authorization* was for, where the credential blob can only be inspected for
    traces of one. For every connector without a callback param -- all of them
    but GitHub today -- this is exactly `resolve_external_ref`.
    """
    return _from_callback(connector_id, callback_url) or resolve_external_ref(
        connector_id, credentials
    )
