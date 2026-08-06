"""Session-scoped GitHub credential delivery for `git`/`gh` CLI use in a workspace.

Every other connector's credential is used exclusively through the in-process
tool-call proxy (`agent/tools/connectors/`): the account is resolved and the
raw token is used server-side to make one HTTP call, and it never enters the
sandbox. That doesn't work for `git`/`gh`, which are open-ended shell tools --
the credential material has to actually be present inside the sandbox for the
shell to use it.

This mirrors the workspace module's own existing pattern for its own
delegated identity token (`WorkspaceSandboxService.get_env_vars` mints a
short-lived `LEMMA_TOKEN` once per session) and AgentBox's stated design
principle that dynamic credentials belong to a session, never to a sandbox's
persisted profile: the token is written once per session into `/tmp` (which
does not survive a workspace recreation, unlike the durable `/workspace`
volume), re-provisioned periodically rather than trusted forever, and never
returned to the caller, logged, or placed in a tool-result string.
"""

from __future__ import annotations

import re

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


async def ensure_github_credentials(ctx: BaseAgentContext, workspace_session) -> None:
    """Provision a session-scoped GitHub credential file, once per session/TTL.

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
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}"
    if await redis.exists(marker_key):
        return

    token = await _resolve_github_access_token(ctx)
    if token is None:
        await redis.set(marker_key, "unavailable", ex=_UNAVAILABLE_TTL_SECONDS)
        return

    await workspace_session.write_file(
        _CREDENTIALS_PATH,
        f"https://x-access-token:{token}@github.com\n".encode(),
    )
    await workspace_session.exec_command(
        cmd=(
            "git config --global credential.helper "
            f"'store --file={_CREDENTIALS_PATH}' && chmod 600 {_CREDENTIALS_PATH}"
        ),
        timeout=15,
    )
    await redis.set(marker_key, "provisioned", ex=_PROVISIONED_TTL_SECONDS)


async def _resolve_github_access_token(ctx: BaseAgentContext) -> str | None:
    from app.modules.connectors.api.dependencies import get_account_resolution_service

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
                )
            except (AccountResolutionError, ConnectorAccessDeniedError):
                return None
            credentials = account.credentials
            access_token = getattr(credentials, "access_token", None)
            return access_token if isinstance(access_token, str) and access_token else None
        finally:
            reset_current_context(token)
