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
from dataclasses import dataclass

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.infrastructure.db.session import async_session_maker
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

logger = get_logger(__name__)

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
# Re-provision periodically rather than trusting a stale/possibly-revoked
# token forever. GitHub OAuth App tokens do not expire on their own, so this
# is a safety net (account disconnected, token rotated), not expiry handling.
_PROVISIONED_TTL_SECONDS = 45 * 60
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


def looks_like_git_command(cmd: str) -> bool:
    return bool(_GIT_COMMAND_PATTERN.search(cmd))


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


async def ensure_github_credentials(ctx: BaseAgentContext, workspace_session) -> None:
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

    redis = get_redis(url=settings.redis_url)
    # The account is part of the marker: a conversation bound to a project names
    # the account it works as, and two conversations in one session must not
    # inherit each other's credential file.
    account_id = ctx.workspace_repo.account_id if ctx.workspace_repo else None
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}:{account_id or 'default'}"
    if await redis.exists(marker_key):
        return

    credential = await _resolve_github_credential(ctx)
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
    git_email = credential.email or (
        f"{credential.login}@users.noreply.github.com" if credential.login else None
    )
    if git_email:
        setup_commands.append(
            f"git config --global user.email {shlex.quote(git_email)}"
        )
    await workspace_session.exec_command(cmd=" && ".join(setup_commands), timeout=15)

    await redis.set(marker_key, "provisioned", ex=_PROVISIONED_TTL_SECONDS)


async def _resolve_github_credential(ctx: BaseAgentContext) -> _GithubCredential | None:
    from app.modules.connectors.api.dependencies import get_account_resolution_service

    # A project names the account it is worked as. Without one, resolution picks
    # for a user who may have connected GitHub twice -- fine as a fallback, but
    # never the right answer when the caller actually knows.
    account_id = ctx.workspace_repo.account_id if ctx.workspace_repo else None
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        auth_ctx = await build_delegated_context(uow, ctx)
        token = set_current_context(auth_ctx)
        try:
            account_resolution = get_account_resolution_service(uow)
            try:
                account = await account_resolution.resolve_account(
                    user_id=ctx.user_id,
                    connector_id=_CONNECTOR_ID,
                    auth_actor=auth_ctx,
                    account_id=account_id,
                )
            except (AccountResolutionError, ConnectorAccessDeniedError):
                return None
            credentials = account.credentials
            access_token = getattr(credentials, "access_token", None)
            if not isinstance(access_token, str) or not access_token:
                return None
            return _GithubCredential(
                access_token=access_token,
                login=account.display_name,
                email=account.email,
            )
        finally:
            reset_current_context(token)
