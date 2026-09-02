"""Updating an install in place, without disconnecting the people using it.

Before this, changing anything about an install meant deleting and recreating
it, and ``accounts.auth_config_id`` is ``ON DELETE CASCADE`` -- so rotating an
MCP server's URL silently disconnected every user who had connected to it, took
their grants with it, and left every schedule and surface holding a dangling
account id. The operation an admin reaches for most often was the single most
destructive one available.

The rule here is that an update never deletes an account. Where new config
genuinely invalidates a stored credential, the account is marked
``REAUTH_REQUIRED`` instead: the row, its id, and every reference to it survive,
and the existing connect flow already updates an unhealthy account *in place* on
reconnect. So the worst an update can do is ask people to reconnect, which is
recoverable, rather than delete something, which is not.

Deciding when a credential is actually invalidated is the whole content of this
module. Guessing "always" would make the endpoint useless -- a typo fix in a
description would sign everyone out. Guessing "never" would leave accounts
looking healthy while every call 401s. So it is answered per kind, from what the
credential is actually bound to:

* ``mcp``/``http`` -- a bearer token is issued by whoever runs the server, so it
  survives a path or query change but not a move to a different origin.
* ``sql`` -- a username/password pair is defined inside one database on one
  host; either changing means the credential is for somewhere else.
* OAuth kinds -- tokens are bound to the client that issued them, so swapping
  the org's OAuth app invalidates every one of them.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.connector import ConnectorEntity, ConnectorKind
from app.modules.connectors.services.install_service_seam import (
    InstallServiceSeam,
)

logger = get_logger(__name__)

# Config keys that name where we connect to, per kind. A change to any of them
# means the credential now points at a different system.
_TARGET_FIELDS: dict[ConnectorKind, tuple[str, ...]] = {
    ConnectorKind.MCP: ("server_url",),
    ConnectorKind.HTTP: ("server_url",),
    ConnectorKind.SQL: ("host", "port", "database"),
}

# Config keys that decide what operations exist. Changing one means the stored
# operation set describes a server we are no longer talking to.
_DISCOVERY_FIELDS: dict[ConnectorKind, tuple[str, ...]] = {
    ConnectorKind.MCP: ("server_url", "extra_headers"),
    ConnectorKind.HTTP: ("server_url", "spec_url", "spec_inline"),
}


def _origin(url: str) -> tuple[str, str, int | None]:
    """The part of a URL a bearer token is issued against."""
    parts = urlsplit(url)
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port)


def _target_changed(
    kind: ConnectorKind, before: dict[str, Any], after: dict[str, Any]
) -> bool:
    fields = _TARGET_FIELDS.get(kind)
    if not fields:
        return False
    for field in fields:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        # A URL is compared by origin: repointing at a different host is a
        # different system, but correcting a path on the same server is not,
        # and treating that as a credential change would sign everyone out
        # over a typo fix.
        if field.endswith("_url") and isinstance(old, str) and isinstance(new, str):
            if _origin(old) == _origin(new):
                continue
        return True
    return False


def _oauth_credentials_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Whether the org swapped the OAuth app that issued the existing tokens."""

    def creds(config: dict[str, Any]) -> dict[str, Any]:
        nested = config.get("oauth2_credentials")
        return nested if isinstance(nested, dict) else config

    old, new = creds(before), creds(after)
    return any(old.get(key) != new.get(key) for key in ("client_id", "client_secret"))


def config_change_effects(
    *,
    kind: ConnectorKind,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[bool, bool]:
    """Return ``(rediscover, invalidates_credentials)`` for a config change."""
    old = dict(before or {})
    new = dict(after or {})
    if old == new:
        return (False, False)

    discovery_fields = _DISCOVERY_FIELDS.get(kind, ())
    rediscover = any(old.get(field) != new.get(field) for field in discovery_fields)

    invalidates = _target_changed(kind, old, new) or _oauth_credentials_changed(
        old, new
    )
    return (rediscover, invalidates)


def apply_updates(
    auth_config: AuthConfigEntity,
    *,
    name: str | None,
    config: dict[str, Any] | None,
    status: AuthConfigStatus | None,
    is_default: bool | None,
    updated_by_user_id: UUID | None,
) -> AuthConfigEntity:
    """Return the install with the supplied fields applied.

    ``kind``, ``connector_id`` and ``config_source`` are absent on purpose.
    Changing any of them would reinterpret every stored operation and
    credential, which is a different install -- so it is a create, not an
    update, and the caller keeps their existing one until they are ready.
    """
    if name is not None:
        auth_config.name = name
    if config is not None:
        # Already merged by the caller, which needs the merged shape to decide
        # what the change invalidates.
        auth_config.config = config or None
    if status is not None:
        auth_config.status = status
    if is_default is not None:
        auth_config.is_default = is_default
    auth_config.updated_by_user_id = updated_by_user_id
    return auth_config


# What `_redact_config` puts in place of a secret on the way out.
_REDACTION_MASK = "********"


def merged_install_config(
    stored: dict[str, Any] | None, submitted: dict[str, Any]
) -> dict[str, Any]:
    """Apply a submitted config without destroying what it could not carry.

    A plain replace was wrong twice over, and a GET-edit-PATCH round trip --
    which is what the UI does -- hit both.

    Secrets come back from the API masked, so re-submitting the form wrote the
    literal ``********`` over the real value. An MCP install's
    ``extra_headers`` lost its Authorization header to a string of asterisks,
    and nothing said so: `_target_changed` only watches the server URL, so no
    account was marked and every later call simply failed. This is the same
    bug `runtime_profile_repository` documents having fixed on its own path;
    the fix had not reached here.

    And keys the system wrote are not in the user's form at all. MCP OAuth
    registration stores an ``oauth`` block -- issuer, endpoints, a dynamically
    registered client id and secret -- after validation, and only ever at
    create time. A replace dropped it, and nothing re-negotiates on update, so
    an install that had been signed into reverted to a paste-a-token one that
    could no longer refresh anybody's credential. The install schema declares
    what the person owns; anything else on the record was put there for them
    and survives an edit they never saw it in.
    """
    existing = dict(stored or {})
    merged = dict(existing)
    for key, value in submitted.items():
        merged[key] = _unmasked(existing.get(key), value)
    return merged


def _unmasked(stored: Any, submitted: Any) -> Any:
    """``submitted``, with any redaction mask replaced by what was stored."""
    if submitted == _REDACTION_MASK:
        return stored if stored is not None else submitted
    if isinstance(submitted, dict):
        nested = stored if isinstance(stored, dict) else {}
        return {
            key: _unmasked(nested.get(key), value) for key, value in submitted.items()
        }
    return submitted


async def mark_accounts_for_reauth(
    account_repository: Any, *, auth_config_id: UUID
) -> int:
    """Flag every connected account on this install as needing a reconnect.

    Deliberately not a delete and not a credential wipe. The account keeps its
    id, its grants, and its stored credentials, so anything referencing it --
    a schedule, a surface, a pod-bundle variable -- still resolves, and the
    reconnect updates the row in place rather than creating a second one.
    """
    accounts = await account_repository.list_by_auth_config(auth_config_id)
    marked = 0
    for account in accounts:
        if account.status != AccountStatus.CONNECTED:
            continue
        account.status = AccountStatus.REAUTH_REQUIRED
        await account_repository.update(account)
        marked += 1
    return marked


async def _clear_default_install(
    service: InstallServiceSeam,
    *,
    organization_id: UUID,
    connector_id: str,
    keep_id: UUID,
) -> None:
    """Demote whichever install currently answers a bare connector_id.

    A partial unique index allows exactly one default per (org, connector), so
    promoting a second one has to demote the first in the same transaction or
    the write is rejected outright.
    """
    current = await service.auth_config_repository.get_active_by_org_and_app(
        organization_id, connector_id
    )
    if current is None or current.id == keep_id or not current.is_default:
        return
    current.is_default = False
    await service.auth_config_repository.update(current)


async def update_install(
    service: InstallServiceSeam,
    *,
    user_id: UUID,
    organization_id: UUID,
    auth_config_name: str,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    status: str | None = None,
    is_default: bool | None = None,
) -> tuple[AuthConfigEntity, int, int]:
    """Apply an update, returning ``(install, discovered, accounts_marked)``.

    The counts are returned rather than logged and forgotten so an admin is
    told what their change actually did -- particularly that some accounts now
    need reconnecting -- instead of finding out when something stops working.
    """
    await service._require_org_member(
        user_id=user_id,
        organization_id=organization_id,
        allowed_roles=["ORG_OWNER", "ORG_EDITOR"],
    )
    auth_config = await service._resolve_auth_config(
        organization_id=organization_id, auth_config_name=auth_config_name
    )
    connector = await service.get_connector(auth_config.connector_id)

    rediscover = invalidates = False
    if config is not None:
        validated = await validate_updated_config(
            connector=connector, auth_config=auth_config, config=config
        )
        # Against the MERGED result, not the submitted shape. A form submits a
        # partial config -- that is why the merge exists -- so comparing the
        # submission directly reads every omitted key as a change: a PATCH that
        # only renames an install marked every account REAUTH_REQUIRED and
        # re-ran discovery, for a target that had not moved.
        config = merged_install_config(auth_config.config, validated)
        rediscover, invalidates = config_change_effects(
            kind=auth_config.kind, before=auth_config.config, after=config
        )

    if is_default:
        await _clear_default_install(
            service,
            organization_id=organization_id,
            connector_id=auth_config.connector_id,
            keep_id=auth_config.id,
        )

    auth_config = apply_updates(
        auth_config,
        name=name,
        config=config,
        status=AuthConfigStatus(status) if status else None,
        is_default=is_default,
        updated_by_user_id=user_id,
    )
    auth_config = await service.auth_config_repository.update(auth_config)

    marked = 0
    if invalidates:
        marked = await mark_accounts_for_reauth(
            service.account_repository, auth_config_id=auth_config.id
        )
    await service.uow.commit()

    discovered = 0
    if rediscover:
        from app.modules.connectors.services.install_provisioning import (
            discover_install_operations,
            discovery_credentials,
        )

        # With a credential, for the same reason `refresh_install_operations`
        # uses one: an install whose token lives on the account cannot list its
        # operations unauthenticated, and the 401 is swallowed into
        # `discovered=0`. Without this, repointing an OAuth-protected MCP
        # install reports success while leaving the OLD server's tool list in
        # place, so every later call names a tool the new host does not have.
        discovered = await discover_install_operations(
            auth_config,
            connector,
            repository=service.auth_config_operation_repository,
            uow=service.uow,
            credentials=await discovery_credentials(service, auth_config),
        )
    logger.info(
        "connectors.connector_service.auth_config_updated",
        auth_config_id=auth_config.id,
        organization_id=organization_id,
        operations_discovered=discovered,
        accounts_marked_for_reauth=marked,
    )
    return auth_config, discovered, marked


async def validate_updated_config(
    *,
    connector: ConnectorEntity,
    auth_config: AuthConfigEntity,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Re-run install validation, including the network-target guard.

    An update is exactly as good a way to point an install at the metadata
    service as a create, so it goes through the same validator rather than
    trusting that the install was vetted once.
    """
    from app.modules.connectors.services.install_provisioning import (
        validate_install_config,
    )

    return await validate_install_config(
        connector=connector,
        kind=auth_config.kind,
        config=config,
        config_source=auth_config.config_source,
    )
