"""Session-scoped GitHub credential delivery for `git`/`gh` CLI use in a workspace.

Every other connector's credential is used exclusively through the in-process
tool-call proxy (`agent/tools/connectors/`): the account is resolved and the
raw token is used server-side to make one HTTP call, and it never enters the
sandbox. That doesn't work for `git`/`gh`, which are open-ended shell tools --
the credential material has to actually be present inside the sandbox for the
shell to use it.

This mirrors the workspace module's own existing pattern for its own
delegated identity token (`WorkspaceSandboxService.get_env_vars` mints a
short-lived `LEMMA_TOKEN` once per session) and the sandbox runtime's stated design
principle that dynamic credentials belong to a session, never to a sandbox's
persisted profile: the token is written once per session into `/tmp` (which
does not survive a workspace recreation, unlike the durable `/workspace`
volume), re-provisioned periodically rather than trusted forever, and never
returned to the caller, logged, or placed in a tool-result string.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.context import Context
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.connectors.connector_access import build_delegated_context
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)


class _ResolvedAccount(Protocol):
    """What account resolution hands back, as far as this bridge is concerned."""

    credentials: object
    display_name: str | None
    email: str | None


class _AccountResolution(Protocol):
    """The single connector-side call this makes.

    Named here rather than imported: `AccountResolutionService` lives inside
    the connectors module, and the concrete factory for it is imported lazily
    below to avoid an import cycle.
    """

    async def resolve_account(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        auth_actor: Context,
        account_id: UUID | None,
    ) -> _ResolvedAccount: ...


_CONNECTOR_ID = "github"
_CREDENTIALS_PATH = "/tmp/.git-credentials"
# `gh` reads its own config; it does not understand git's credential file. The
# alternative -- exporting GH_TOKEN from a shell profile -- would put the raw
# token in the environment of every process the agent starts, so an ordinary
# `env` would print it straight into a tool result and the transcript. Keeping
# it in a file `gh` reads confines it to the same place git's copy already
# lives. `GH_CONFIG_DIR` in the workspace image points here.
_GH_CONFIG_DIR = "/tmp/lemma-gh"
_GH_HOSTS_PATH = f"{_GH_CONFIG_DIR}/hosts.yml"
_MARKER_KEY_PREFIX = "workspace:github-credentials:v1"
# How long a copy of the token written into the sandbox may be trusted.
#
# This used to be a flat 45 minutes, reasoning that "GitHub OAuth App tokens do
# not expire on their own". That is true of an OAuth App and false here: this
# connector is a GitHub *App* -- `services/auth/github_app.py` exists precisely
# because it is -- and its user tokens expire in eight hours. The marker was
# therefore not expiry handling at all, and the bridge re-wrote the same dead
# token into the workspace every 45 minutes once it went stale.
#
# So the ceiling stays as the safety net it was described as (an account
# disconnected upstream, a token revoked -- neither of which changes an expiry),
# and the actual expiry, when the provider states one, is what bounds it.
_PROVISIONED_TTL_SECONDS = 45 * 60
# Re-provision this far before the token dies, so a command that starts just
# inside the window does not run past it.
_EXPIRY_SKEW_SECONDS = 5 * 60
# Below this there is nothing worth writing: whatever we put in the sandbox
# would be dead before it was used.
_MIN_PROVISION_SECONDS = 30
# A failed resolution (no connected account, not authorized) is cached too, so
# a session running several git commands in a row doesn't repeat the same DB
# round trip and authorization check for every single one of them.
_UNAVAILABLE_TTL_SECONDS = 5 * 60

# Matches a `git`/`gh` invocation as a whole word at the start of the command
# or after a shell separator -- good enough to gate a cheap, idempotent setup
# step. False negatives (git invoked indirectly through a script) just mean
# the command fails with its own native auth error, same as it would without
# this bridge at all.
_GIT_COMMAND_PATTERN = re.compile(r"(?:^|[;&|(]|\s)(?:git|gh)\s")

# The same question asked of Python source, where there is no command line to
# read. An agent that shells out to git from `execute_python` needs the same
# credential file as one that types it into a shell, and used to get it only if
# something else in the session had already provisioned -- so the very first git
# call of a session failed if it happened to be made this way.
#
# Matched inside string literals on purpose: that is where a command being
# handed to `subprocess` lives. Wrong guesses are cheap in both directions -- a
# false positive provisions a file nothing reads, a false negative fails with
# git's own auth error exactly as it does today.
_GIT_IN_SOURCE_PATTERN = re.compile(
    r"""["'\s(\[]\s*(?:git|gh)(?:\s|["'])|\bgitpython\b|\bdulwich\b""",
    re.IGNORECASE,
)


def looks_like_git_command(cmd: str) -> bool:
    return bool(_GIT_COMMAND_PATTERN.search(cmd))


def source_may_use_git(code: str) -> bool:
    """Whether this Python is likely to reach GitHub before it finishes."""
    return bool(_GIT_IN_SOURCE_PATTERN.search(code or ""))


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


async def ensure_github_credentials(
    ctx: BaseAgentContext,
    workspace_session,
    *,
    redis: "Redis | None" = None,
    resolve_credential: Callable[
        [BaseAgentContext], Awaitable["_GithubCredential | None"]
    ]
    | None = None,
) -> None:
    """Provision a session-scoped GitHub credential file, once per session/TTL.

    Also configures a git commit identity (`user.name`/`user.email`) from the
    connected account so an agent never has to discover and set this itself
    before its first commit -- the exact manual step this function exists to
    make unnecessary.

    Only the "no connected account" / "not authorized" outcome is treated as
    a stable result worth caching as unavailable. Any other failure (Redis or
    DB unreachable, a write to the sandbox failing) is left uncached so the
    next git-looking command retries rather than being permanently treated as
    unavailable for the rest of the session -- and, deliberately, is not
    swallowed here: the caller decides whether a broken credential bridge
    should still let the underlying `git`/`gh` command run without
    credentials (it should -- see `exec_command_internal`), which requires
    letting the exception surface up to it rather than hiding it in this
    function.
    """
    session_id = workspace_session.session_id
    if not session_id:
        return

    # Both collaborators are arguments so a test can watch this function's own
    # decisions -- the marker key, which outcome is cacheable, what lands in
    # the two files -- instead of replacing them inside it. Resolved after the
    # session check, so no client is built for a call that does nothing.
    if redis is None:
        redis = get_redis(url=settings.redis_url)
    if resolve_credential is None:
        resolve_credential = _resolve_github_credential
    # The account is part of the marker: a conversation bound to a project names
    # the account it works as, and two conversations in one session must not
    # inherit each other's credential file.
    account_id = ctx.workspace_repo.account_id if ctx.workspace_repo else None
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}:{account_id or 'default'}"
    if await redis.exists(marker_key):
        return

    credential = await resolve_credential(ctx)
    if credential is None:
        await redis.set(marker_key, "unavailable", ex=_UNAVAILABLE_TTL_SECONDS)
        return

    await workspace_session.write_file(
        _CREDENTIALS_PATH,
        f"https://x-access-token:{credential.access_token}@github.com\n".encode(),
    )
    # Same credential, in the form `gh` reads. Written rather than passed
    # through a shell command so the token never appears in an argument list.
    await workspace_session.write_file(
        _GH_HOSTS_PATH,
        (
            "github.com:\n"
            f"    oauth_token: {credential.access_token}\n"
            f"    user: {credential.login or 'x-access-token'}\n"
            "    git_protocol: https\n"
        ).encode(),
    )

    setup_commands = [
        f"git config --global credential.helper 'store --file={_CREDENTIALS_PATH}'",
        f"chmod 600 {_CREDENTIALS_PATH}",
        f"chmod 700 {_GH_CONFIG_DIR}",
        f"chmod 600 {_GH_HOSTS_PATH}",
    ]
    if credential.login:
        setup_commands.append(
            f"git config --global user.name {shlex.quote(credential.login)}"
        )
    # GitHub commonly withholds email from the profile response when the
    # account has "keep my email address private" enabled -- not an error,
    # just no address to use, so fall back to GitHub's own noreply
    # convention (the same address `git commit` shows in the GitHub UI as a
    # verified author for commits made this way) rather than leaving the
    # commit identity half-configured.
    git_email = credential.email or _noreply_email(credential)
    if git_email:
        setup_commands.append(
            f"git config --global user.email {shlex.quote(git_email)}"
        )
    await workspace_session.exec_command(cmd=" && ".join(setup_commands), timeout=15)

    await redis.set(marker_key, "provisioned", ex=_provisioned_ttl(credential))


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


def _provisioned_ttl(credential: "_GithubCredential") -> int:
    """How long the copy in the sandbox may be trusted before it is rewritten.

    The provider's own expiry when there is one, less a margin so a command
    starting just inside the window does not run past it; the flat ceiling
    otherwise, which is all a non-expiring credential needs.
    """
    if credential.expires_at is None:
        return _PROVISIONED_TTL_SECONDS
    remaining = (credential.expires_at - datetime.now(timezone.utc)).total_seconds()
    usable = int(remaining) - _EXPIRY_SKEW_SECONDS
    return max(_MIN_PROVISION_SECONDS, min(_PROVISIONED_TTL_SECONDS, usable))


def _noreply_email(credential: "_GithubCredential") -> str | None:
    """GitHub's own address for an account that keeps its email private.

    Withholding the address is normal rather than an error -- "keep my email
    address private" -- and leaving the commit identity half-configured would
    make the agent's first `git commit` fail with git's own "Please tell me who
    you are". The numeric id is part of the address on purpose: the bare
    `{login}@` form stops associating commits with the account after a rename.
    """
    if not credential.login:
        return None
    if credential.user_id:
        return f"{credential.user_id}+{credential.login}@users.noreply.github.com"
    return f"{credential.login}@users.noreply.github.com"


async def _resolve_github_credential(
    ctx: BaseAgentContext,
    *,
    uow_factory: Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]
    | None = None,
    delegated_context: Callable[
        [SqlAlchemyUnitOfWork, BaseAgentContext], Awaitable[Context]
    ] = build_delegated_context,
    account_resolution: Callable[[SqlAlchemyUnitOfWork], _AccountResolution]
    | None = None,
    refresh_credentials: Callable[
        [SqlAlchemyUnitOfWork, object, UUID], Awaitable[dict[str, object] | None]
    ]
    | None = None,
) -> _GithubCredential | None:
    """Resolve the connected GitHub account's token for this conversation.

    The unit of work and the two connector collaborators are arguments so this
    can be exercised without a database: it used to build
    ``SessionUnitOfWorkFactory(async_session_maker)()`` itself, and its tests
    entered a real async session on the argument that nothing inside would
    execute a query -- which is a property of the code under test, not
    something the test could hold.
    """
    if account_resolution is None:
        # Imported here rather than at module scope to avoid an import cycle.
        from app.modules.connectors.api.dependencies import (
            get_account_resolution_service,
        )

        account_resolution = get_account_resolution_service
    if uow_factory is None:
        uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    if refresh_credentials is None:
        refresh_credentials = _refreshed_credentials

    # A project names the account it is worked as. Without one, resolution picks
    # for a user who may have connected GitHub twice -- fine as a fallback, but
    # never the right answer when the caller actually knows.
    account_id = ctx.workspace_repo.account_id if ctx.workspace_repo else None
    async with uow_factory() as uow:
        auth_ctx = await delegated_context(uow, ctx)
        token = set_current_context(auth_ctx)
        try:
            resolution = account_resolution(uow)
            try:
                account = await resolution.resolve_account(
                    user_id=ctx.user_id,
                    connector_id=_CONNECTOR_ID,
                    auth_actor=auth_ctx,
                    account_id=account_id,
                )
            except AccountResolutionError, ConnectorAccessDeniedError:
                return None
            # Refreshed if it is due, exactly like every connector operation.
            # This path used to read `account.credentials` verbatim, which is
            # how an expired token kept being written into the workspace: the
            # refresh machinery existed, and only the sandbox did not use it.
            credentials = await refresh_credentials(uow, account, ctx.user_id)
            access_token = (credentials or {}).get("access_token")
            reveal = getattr(access_token, "get_secret_value", None)
            if callable(reveal):
                access_token = reveal()
            if not isinstance(access_token, str) or not access_token:
                return None
            return _GithubCredential(
                access_token=access_token,
                login=account.display_name,
                email=account.email,
                user_id=_github_user_id(credentials),
                expires_at=_expires_at(credentials),
            )
        finally:
            reset_current_context(token)
