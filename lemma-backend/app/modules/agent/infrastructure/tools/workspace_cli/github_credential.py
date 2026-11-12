"""Which GitHub account a pod's sandbox holds, and for whom.

The deciding half of the credential bridge: ``github_credential_bridge`` writes
the answer into a workspace. Apart because it has a second caller -- a
conversation opened in a repository asks it, as the person opening it, before
anything is cloned, so the account the repository is checked against is the
account the clone then uses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.contracts.pod_connectors import (
    acting_person,
    resolve_pod_connector,
)

#: The pod connector slug the sandbox's git and gh run through.
_POD_CONNECTOR_SLUG = "github"


class _ResolvedConnector(Protocol):
    """What resolving the pod's `github` connector hands back, as far as this
    bridge is concerned: whose account it is, and the account."""

    owner_user_id: UUID
    pod_account_id: UUID
    #: The owner's "allow use inside sandboxes" on the pod account.
    allow_sandbox_use: bool
    account: object


class _ResolveConnector(Protocol):
    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        ctx: Context,
        *,
        pod_id: UUID,
        slug: str,
    ) -> _ResolvedConnector: ...


@dataclass(frozen=True, slots=True)
class GithubRefusal:
    """Why no credential was written, for the agent to read.

    The pod connector's own typed answer -- connect your account, reconnect
    it, this pod declares no `github` -- carried as data so the agent can act
    on it, with the link when there is one. It used to be a cached
    "unavailable" and nothing else, so git simply failed with its own auth
    error and nobody was told what to do about it.
    """

    code: str
    message: str
    connect_url: str | None = None
    #: The refusal's own status, for a caller that answers over HTTP.
    status_code: int = 409

    def notice(self) -> str:
        text = (
            f"[workspace notice] No GitHub credential in this workspace: {self.message}"
        )
        if self.connect_url:
            text += f" Connect it at {self.connect_url}"
        return text


@dataclass(frozen=True, slots=True)
class _GithubCredential:
    access_token: str
    # The account's GitHub login (`display_name`, resolved at connect time via
    # the catalog-curated `users_get_authenticated` profile operation --
    # see AccountIdentity/_github_identity). None only for an account
    # connected before that profile enrichment existed; the bridge still
    # works (the credential file is what git actually needs), it just can't
    # set a commit identity, so `git commit` fails with git's own
    # "Please tell me who you are" until the agent sets one itself.
    login: str | None
    email: str | None
    # GitHub's numeric account id. The noreply address needs it: the bare
    # `{login}@users.noreply.github.com` form stops associating commits with
    # the account the moment somebody renames themselves, and GitHub documents
    # the `{id}+{login}` form as the one that survives it.
    user_id: str | None = None
    # When the copy written into the sandbox stops working, if GitHub said.
    expires_at: datetime | None = None
    # The GitHub App installation the account is bound to (its
    # `external_ref`): what decides which repositories the token reaches.
    installation_id: str | None = None
    # Set when the account is somebody else's, lent to sandboxes by its owner.
    borrowed_pod_account_id: UUID | None = None
    # The person it was lent to: whom the rule is re-run for on each use.
    borrower_user_id: UUID | None = None


async def github_credential_as(
    uow: SqlAlchemyUnitOfWork,
    auth_ctx: Context,
    *,
    pod_id: UUID,
    user_id: UUID,
    resolve_connector: _ResolveConnector | None = None,
    refresh_credentials: Callable[
        [SqlAlchemyUnitOfWork, object, UUID], Awaitable[dict[str, object] | None]
    ]
    | None = None,
) -> _GithubCredential | GithubRefusal | None:
    """The token this pod's sandbox would hold for whoever ``auth_ctx`` is.

    The bridge's whole rule, with the caller choosing the authority: a run's
    tools pass the run's delegated context, and a conversation being opened
    in a repository passes the person opening it, so that what they pick is
    checked against the same account and the same sandbox rule as the clone
    that follows. Bound to ``auth_ctx`` by the caller, who owns the unit of
    work.
    """
    if resolve_connector is None:
        resolve_connector = resolve_pod_connector
    if refresh_credentials is None:
        refresh_credentials = _refreshed_credentials
    try:
        resolved = await resolve_connector(
            uow, auth_ctx, pod_id=pod_id, slug=_POD_CONNECTOR_SLUG
        )
    except DomainError as refused:
        return _refusal(refused)
    borrowed = resolved.owner_user_id != acting_person(auth_ctx)
    if borrowed and not resolved.allow_sandbox_use:
        return GithubRefusal(
            code="SHARED_ACCOUNT_NOT_FOR_SANDBOX",
            status_code=403,
            message=(
                "this pod's 'github' connector runs as an account somebody "
                "shared with the pod, and a shared account is not put "
                "inside a workspace unless its owner allows use inside "
                "sandboxes. Use the GitHub connector operations instead, "
                "ask the account's owner to allow it, or ask a pod "
                "editor for a per-member 'github' connector."
            ),
        )
    account = resolved.account
    # Refreshed if it is due, exactly like every connector operation: an
    # expired token written into the workspace is a dead one.
    try:
        credentials = await refresh_credentials(uow, account, user_id)
    except DomainError as refused:
        return _refusal(refused)
    access_token = (credentials or {}).get("access_token")
    reveal = getattr(access_token, "get_secret_value", None)
    if callable(reveal):
        access_token = reveal()
    if not isinstance(access_token, str) or not access_token:
        return None
    installation = getattr(account, "external_ref", None)
    return _GithubCredential(
        access_token=access_token,
        login=getattr(account, "display_name", None),
        email=getattr(account, "email", None),
        user_id=_github_user_id(credentials),
        expires_at=_expires_at(credentials),
        installation_id=str(installation) if installation else None,
        borrowed_pod_account_id=resolved.pod_account_id if borrowed else None,
        borrower_user_id=acting_person(auth_ctx) if borrowed else None,
    )


def _refusal(refused: DomainError) -> GithubRefusal:
    details = refused.details if isinstance(refused.details, dict) else {}
    connect_url = details.get("connect_url")
    return GithubRefusal(
        code=str(refused.code),
        message=str(refused.message),
        connect_url=connect_url if isinstance(connect_url, str) else None,
        status_code=refused.status_code,
    )


async def _refreshed_credentials(
    uow: SqlAlchemyUnitOfWork, account: object, user_id: UUID
) -> dict[str, object] | None:
    """This account's credentials, renewed if they are due to expire.

    A collaborator rather than a direct call, like everything else this module
    reaches for: building a `ConnectorService` is the one step here that needs a
    real unit of work, and a test of the bridge's own decisions should not have
    to supply one.
    """
    # Imported here rather than at module scope to avoid an import cycle.
    from app.modules.connectors.contracts.credentials import (
        fresh_account_credentials,
    )

    return await fresh_account_credentials(uow, account, user_id)


def _expires_at(credentials: dict[str, object] | None) -> "datetime | None":
    from app.modules.connectors.contracts.credentials import credentials_expire_at

    return credentials_expire_at(credentials)


def _github_user_id(credentials: dict[str, object] | None) -> str | None:
    """GitHub's numeric id for the account, out of the stored profile.

    `provider_account_id` is the *login* for this connector, which is the right
    handle for everything else and the wrong one here: the noreply address that
    survives a rename is keyed by the number.
    """
    user_data = (credentials or {}).get("user_data")
    if not isinstance(user_data, dict):
        return None
    profile = user_data.get("profile")
    if not isinstance(profile, dict):
        return None
    identifier = profile.get("id")
    return str(identifier) if identifier is not None else None
