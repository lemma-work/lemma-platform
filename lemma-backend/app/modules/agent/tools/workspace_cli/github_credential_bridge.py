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
does not survive a workspace recreation, unlike the durable home
volume), re-provisioned periodically rather than trusted forever, and never
returned to the caller, logged, or placed in a tool-result string.

Into *that session's* directory under `/tmp`, and configured through files the
session's own environment names (see `workspace/contracts/session_config`).
A person has one sandbox and every agent session of theirs runs in it, so a
credential file or a `git config --global` shared by the sandbox was whichever
session wrote last: one conversation's push went out as the account another
had just resolved. Nothing here writes global git config any more.
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
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.authority import tool_authorization_context
from app.modules.connectors.contracts.pod_connectors import (
    acting_person,
    resolve_pod_connector,
)
from app.modules.workspace.contracts.session_config import (
    SESSION_CONFIG_ROOT,
    session_config_dir,
    session_gh_config_dir,
    session_git_credentials_path,
    session_gitconfig_path,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.modules.agent.tools.workspace_cli.github_loan import (
        BorrowedLoan,
        LoanCheck,
    )

logger = get_logger(__name__)


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

    def notice(self) -> str:
        text = (
            f"[workspace notice] No GitHub credential in this workspace: {self.message}"
        )
        if self.connect_url:
            text += f" Connect it at {self.connect_url}"
        return text


#: The pod connector slug the sandbox's git and gh run through.
_POD_CONNECTOR_SLUG = "github"
# `gh` reads its own config; it does not understand git's credential file. The
# alternative -- exporting GH_TOKEN from a shell profile -- would put the raw
# token in the environment of every process the agent starts, so an ordinary
# `env` would print it straight into a tool result and the transcript. Keeping
# it in a file `gh` reads confines it to the same place git's copy already
# lives: the session's `GH_CONFIG_DIR`, which its environment points at.
_GH_HOSTS_NAME = "hosts.yml"
#: Named again by ``github_loan`` and ``sandbox_borrowers``, which load only
#: once a session borrows (``test_github_loan`` holds them together).
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
#: Twice the longest a live session goes without its credential being rewritten.
_STALE_SESSION_MINUTES = 2 * _PROVISIONED_TTL_SECONDS // 60
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
# A session given somebody else's account is marked with the loan, re-checked
# against the whole pod-account rule on each later git command (`github_loan`).

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
    # Set when the account is somebody else's, lent to sandboxes by its owner.
    borrowed_pod_account_id: UUID | None = None
    # The person it was lent to: whom the rule is re-run for on each use.
    borrower_user_id: UUID | None = None


async def ensure_github_credentials(
    ctx: BaseAgentContext,
    workspace_session,
    *,
    redis: "Redis | None" = None,
    resolve_credential: Callable[
        [BaseAgentContext],
        Awaitable["_GithubCredential | GithubRefusal | None"],
    ]
    | None = None,
    sandbox_use_allowed: "LoanCheck | None" = None,
) -> str | None:
    """Provision a session-scoped GitHub credential file, once per session/TTL.

    Returns a notice for the agent when the pod connector refused -- once, on
    the call that found out; the refusal is cached like any other
    unavailability, so the commands after it say nothing more.

    Also configures a git commit identity (`user.name`/`user.email`) from the
    connected account so an agent never has to discover and set this itself
    before its first commit -- the exact manual step this function exists to
    make unnecessary.

    Only a refusal (the pod connector's typed answer, or no token on the
    account) is treated as a stable result worth caching as unavailable. Any other failure (Redis or
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
        return None

    # Both collaborators are arguments so a test can watch this function's own
    # decisions -- the marker key, which outcome is cacheable, what lands in
    # the two files -- instead of replacing them inside it. Resolved after the
    # session check, so no client is built for a call that does nothing.
    if redis is None:
        redis = get_redis(url=settings.redis_url)
    if resolve_credential is None:
        resolve_credential = _resolve_github_credential
    # Per session only: which account is written is the pod connector's
    # decision for the person acting, never something a conversation names.
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}"
    marker = await redis.get(marker_key)
    took_back = False
    if marker is not None:
        from app.modules.agent.tools.workspace_cli.github_loan import (
            MarkerVerdict,
            settle_marker,
        )

        verdict = await settle_marker(
            marker.decode() if isinstance(marker, bytes) else str(marker),
            workspace_session=workspace_session,
            session_id=session_id,
            marker_key=marker_key,
            redis=redis,
            still_holds=sandbox_use_allowed,
            unavailable_ttl_seconds=_UNAVAILABLE_TTL_SECONDS,
        )
        if verdict is MarkerVerdict.KEEP:
            return None
        if verdict is MarkerVerdict.WITHDRAWN:
            return _SANDBOX_USE_WITHDRAWN.notice()
        took_back = True

    credential = await resolve_credential(ctx)
    if not isinstance(credential, _GithubCredential):
        await redis.set(marker_key, "unavailable", ex=_UNAVAILABLE_TTL_SECONDS)
        if credential is not None:
            return credential.notice()
        return _SANDBOX_USE_WITHDRAWN.notice() if took_back else None

    credentials_path = session_git_credentials_path(session_id)
    gh_hosts_path = f"{session_gh_config_dir(session_id)}/{_GH_HOSTS_NAME}"
    await workspace_session.write_file(
        credentials_path,
        f"https://x-access-token:{credential.access_token}@github.com\n".encode(),
    )
    # Same credential, in the form `gh` reads. Written rather than passed
    # through a shell command so the token never appears in an argument list.
    await workspace_session.write_file(
        gh_hosts_path,
        (
            "github.com:\n"
            f"    oauth_token: {credential.access_token}\n"
            f"    user: {credential.login or 'x-access-token'}\n"
            "    git_protocol: https\n"
        ).encode(),
    )
    # The helper and the commit identity, as a file this session's git
    # includes (see `session_config_env`) rather than `git config --global`,
    # which every session in the sandbox shares. No token in it.
    await workspace_session.write_file(
        session_gitconfig_path(session_id),
        _session_gitconfig(credential, credentials_path).encode(),
    )

    session_dir = session_config_dir(session_id)
    await workspace_session.exec_command(
        cmd=" && ".join(
            [
                f"chmod 700 {shlex.quote(session_dir)}",
                f"chmod 700 {shlex.quote(session_gh_config_dir(session_id))}",
                f"chmod 600 {shlex.quote(credentials_path)}",
                f"chmod 600 {shlex.quote(gh_hosts_path)}",
                # Other sessions' directories nobody has refreshed in
                # `_STALE_SESSION_MINUTES` are of sessions that ended: a live
                # one is rewritten well within that. Nothing else ever removes
                # them, and each held a GitHub credential readable to any
                # process in the sandbox. This one's was just written.
                # In a subshell, so a failed sweep never hides a failed chmod.
                (
                    f"(find {shlex.quote(SESSION_CONFIG_ROOT)} -mindepth 1 "
                    f"-maxdepth 1 -type d -mmin +{_STALE_SESSION_MINUTES} "
                    "-exec rm -rf {} + 2>/dev/null || true)"
                ),
            ]
        ),
        timeout=15,
    )

    loan = _loan_of(credential, ctx)
    if loan is not None:
        from app.modules.connectors.contracts.sandbox_borrowers import (
            note_sandbox_borrower,
        )

        # Before the marker: a withdrawal landing between the two then finds
        # this session, rather than missing a copy the marker vouches for.
        await note_sandbox_borrower(
            redis, pod_account_id=loan.pod_account_id, session_id=session_id
        )
    marker_value = loan.marker() if loan is not None else "provisioned"
    await redis.set(marker_key, marker_value, ex=_provisioned_ttl(credential))
    return None


_SANDBOX_USE_WITHDRAWN = GithubRefusal(
    code="SHARED_ACCOUNT_NOT_FOR_SANDBOX",
    message=(
        "the account this pod's 'github' connector lent this workspace is no "
        "longer available to it here -- its owner stopped allowing it inside "
        "sandboxes or sharing it with you, or the connector now runs as another "
        "account -- so its token was removed from this workspace. Use the "
        "GitHub connector operations instead."
    ),
)


def _loan_of(
    credential: _GithubCredential, ctx: BaseAgentContext
) -> "BorrowedLoan | None":
    if credential.borrowed_pod_account_id is None:
        return None
    from app.modules.agent.tools.workspace_cli.github_loan import BorrowedLoan

    return BorrowedLoan(
        pod_account_id=credential.borrowed_pod_account_id,
        pod_id=ctx.pod_id,
        borrower=credential.borrower_user_id or ctx.user_id,
    )


def _session_gitconfig(credential: "_GithubCredential", credentials_path: str) -> str:
    """The session's git config: its credential helper and commit identity.

    The empty helper first, because a helper is a list and every config file
    read before this one adds to it -- an older bridge left one in the durable
    ``~/.gitconfig`` pointing at a file another session may since have filled.
    The empty value clears what came before, so this session's is the only one.
    """
    lines = [
        "[credential]",
        '\thelper = ""',
        f"\thelper = {_git_config_value(f'store --file={credentials_path}')}",
    ]
    identity = []
    if credential.login:
        identity.append(f"\tname = {_git_config_value(credential.login)}")
    # GitHub commonly withholds email from the profile response when the
    # account has "keep my email address private" enabled -- not an error,
    # just no address to use, so fall back to GitHub's own noreply
    # convention (the same address `git commit` shows in the GitHub UI as a
    # verified author for commits made this way) rather than leaving the
    # commit identity half-configured.
    git_email = credential.email or _noreply_email(credential)
    if git_email:
        identity.append(f"\temail = {_git_config_value(git_email)}")
    if identity:
        lines += ["[user]", *identity]
    return "\n".join(lines) + "\n"


def _git_config_value(value: str) -> str:
    """A value quoted for a git config file, whatever it contains."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


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
    # Resolved at call time, not bound here. A default is evaluated once, at
    # import, so a test that replaces `tool_authorization_context` on its own
    # module never reaches this -- and the test passes anyway, because the real
    # collaborator failing looks like the fake one failing.
    delegated_context: Callable[
        [SqlAlchemyUnitOfWork, BaseAgentContext], Awaitable[Context]
    ]
    | None = None,
    resolve_connector: _ResolveConnector | None = None,
    refresh_credentials: Callable[
        [SqlAlchemyUnitOfWork, object, UUID], Awaitable[dict[str, object] | None]
    ]
    | None = None,
) -> _GithubCredential | GithubRefusal | None:
    """The token of the account the pod's `github` connector resolves to.

    Resolved as the run's actor -- a named agent as its person intersected
    with its own grants, the assistant as the person -- through the same
    resolver as every connector operation, so the pod connector's mode decides
    the account and its typed refusals come back as a `GithubRefusal`.

    Only the acting person's own account is written into a sandbox, or a
    shared one whose owner ticked "allow use inside sandboxes". A token there
    is readable by every later command, and a SHARED connector's account
    belongs to somebody who shared it for operations run through Lemma -- a
    shell holding it is a further thing only they can allow (design §3.8).

    The unit of work and the connector collaborators are arguments so this can
    be exercised without a database.
    """
    if resolve_connector is None:
        resolve_connector = resolve_pod_connector
    if uow_factory is None:
        uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    if refresh_credentials is None:
        refresh_credentials = _refreshed_credentials

    async with uow_factory() as uow:
        auth_ctx = await (delegated_context or tool_authorization_context)(uow, ctx)
        token = set_current_context(auth_ctx)
        try:
            try:
                resolved = await resolve_connector(
                    uow, auth_ctx, pod_id=ctx.pod_id, slug=_POD_CONNECTOR_SLUG
                )
            except DomainError as refused:
                return _refusal(refused)
            borrowed = resolved.owner_user_id != acting_person(auth_ctx)
            if borrowed and not resolved.allow_sandbox_use:
                return GithubRefusal(
                    code="SHARED_ACCOUNT_NOT_FOR_SANDBOX",
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
            # Refreshed if it is due, exactly like every connector operation:
            # an expired token written into the workspace is a dead one.
            try:
                credentials = await refresh_credentials(uow, account, ctx.user_id)
            except DomainError as refused:
                return _refusal(refused)
            access_token = (credentials or {}).get("access_token")
            reveal = getattr(access_token, "get_secret_value", None)
            if callable(reveal):
                access_token = reveal()
            if not isinstance(access_token, str) or not access_token:
                return None
            return _GithubCredential(
                access_token=access_token,
                login=getattr(account, "display_name", None),
                email=getattr(account, "email", None),
                user_id=_github_user_id(credentials),
                expires_at=_expires_at(credentials),
                borrowed_pod_account_id=resolved.pod_account_id if borrowed else None,
                borrower_user_id=acting_person(auth_ctx) if borrowed else None,
            )
        finally:
            reset_current_context(token)


def _refusal(refused: DomainError) -> GithubRefusal:
    details = refused.details if isinstance(refused.details, dict) else {}
    connect_url = details.get("connect_url")
    return GithubRefusal(
        code=str(refused.code),
        message=str(refused.message),
        connect_url=connect_url if isinstance(connect_url, str) else None,
    )
