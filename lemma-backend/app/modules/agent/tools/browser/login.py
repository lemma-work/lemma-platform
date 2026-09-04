"""``browser_login``: get signed in, without the agent ever holding the password.

Three outcomes, in the order they are tried, and the order matters.

**A connector already covers this site.** Then that is the answer, and the agent
is told so rather than being allowed to drive a login form for something OAuth
handles properly. Otherwise people end up taking the wheel to type a Gmail
password that a connector was right there for.

**A saved session exists.** The backend loads it into the sandbox browser and
returns whether it worked. The secret never enters the model's context, the tool
result, or an argument list — see ``web_login/services/injection.py``.

**Nothing is saved, or the session has stopped working.** The tool does not fail;
it opens a takeover and returns the link, so the run's next move is to ask the
person. That is deliberately the same shape as any other question an agent asks,
which is what makes it arrive natively on Slack, WhatsApp and the rest without a
per-platform branch here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent.tools.context import TOOL_COMMENT_DESC, BaseToolResponse

logger = get_logger(__name__)


class BrowserLoginRequest(BaseModel):
    origin: str = Field(
        max_length=255,
        description=(
            "The site to be signed in to, e.g. `https://app.example.com`. The "
            "page you happen to be on does not matter — a login belongs to the "
            "site."
        ),
    )
    reason: str = Field(
        default="",
        max_length=500,
        description=(
            "What you were trying to do. Shown to the person if they are asked "
            "to sign in, so say the task, not the mechanics."
        ),
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserLoginResult(BaseToolResponse):
    origin: Optional[str] = Field(
        default=None, description="The site this is about, normalised."
    )
    signed_in: bool = Field(
        default=False,
        description="Whether the browser now holds a session for this site.",
    )
    needs_person: bool = Field(
        default=False,
        description=(
            "True when somebody has to sign in themselves. Ask them, using the "
            "link — do not ask for their password."
        ),
    )
    takeover_url: Optional[str] = Field(
        default=None,
        description=(
            "Where the person signs in. Send it to them as it is; it only opens "
            "for them."
        ),
    )
    use_connector_instead: Optional[str] = Field(
        default=None,
        description="A connector already covers this site. Use it rather than a browser login.",
    )


def takeover_url(request_id: str) -> str:
    """Where a person goes to sign in.

    Built here rather than handed out by the API because it names a *frontend*
    route, and the backend has exactly two places that encode frontend route
    shapes — this is the second, and it is why the constant lives next to the
    tool that sends it.
    """
    return f"{settings.frontend_url.rstrip('/')}/takeover/{request_id}"


async def login_internal(ctx, request: BrowserLoginRequest) -> BrowserLoginResult:
    """Resolve a login for one origin: a saved session, or ask a person.

    Deliberately three phases with **no database connection held across the
    sandbox round trip**. Loading a session into a browser takes as long as a
    browser takes, and a pooled Postgres connection held for that is one nothing
    else can use. So: read, then inject, then record.

    Runs under the agent's delegated authority, so a workload without
    ``web_login.use`` is refused here rather than at the browser.
    """
    from app.modules.web_login.contracts import (
        InvalidOrigin,
        WebLoginKind,
        inject_web_login,
        normalize_origin,
    )
    from app.modules.workspace.services.takeover import TakeoverStore

    try:
        origin = normalize_origin(request.origin)
    except InvalidOrigin as exc:
        return BrowserLoginResult(success=False, error=str(exc))

    # 1. Read what is saved, then let the connection go.
    saved, secret = await _read_saved_login(ctx, origin)

    # 2. The slow part, with nothing pooled held open.
    outcome = None
    if saved is not None and secret is not None:
        from app.modules.agent.tools.workspace_cli.workspace_cli import (
            get_workspace_session,
            workspace_runtime_context,
        )

        runtime_context = workspace_runtime_context(ctx)
        session = await get_workspace_session(
            ctx,
            session_id=runtime_context.default_shell_session_id,
            close_on_exit=False,
        )
        async with session:
            outcome = await inject_web_login(
                session, secret, kind=WebLoginKind(saved.kind)
            )

    # 3. Record what happened, on a fresh connection.
    if outcome is not None and outcome.injected:
        await _record(
            ctx,
            origin=origin,
            action="inject",
            outcome="ok",
            web_login_id=saved.id if saved else None,
            detail=outcome.reason,
            mark_used=True,
        )
        return BrowserLoginResult(
            success=True,
            signed_in=True,
            origin=origin,
            message=(
                f"Loaded the saved session for {origin}. Open the page and check "
                "you are signed in before going further."
            ),
        )

    if outcome is not None:
        await _record(
            ctx,
            origin=origin,
            action="inject",
            outcome="failed",
            web_login_id=saved.id if saved else None,
            detail=outcome.reason,
        )

    # Nothing saved, or what was saved no longer works. Ask rather than fail:
    # the run continues once somebody has signed in.
    takeover = await TakeoverStore().create(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        origin=origin,
        reason=request.reason,
    )
    await _record(
        ctx,
        origin=origin,
        action="ask",
        outcome="pending",
        web_login_id=saved.id if saved else None,
        detail=request.reason,
    )

    return BrowserLoginResult(
        success=True,
        signed_in=False,
        needs_person=True,
        origin=origin,
        takeover_url=takeover_url(takeover.request_id),
        message=(
            f"Nobody is signed in to {origin} yet. Send the person this link and "
            "wait for them — it opens the browser you are using so they can sign "
            "in themselves. Do not ask them for their password."
        ),
    )


async def _read_saved_login(ctx, origin: str):
    """The saved login and its secret, on a connection that closes immediately."""
    from app.core.authorization.current import (
        reset_current_context,
        set_current_context,
    )
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.tools.connectors.connector_access import (
        build_delegated_context,
    )
    from app.modules.web_login.contracts import WebLoginRepository

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        auth_ctx = await build_delegated_context(uow, ctx)
        token = set_current_context(auth_ctx)
        try:
            repository = WebLoginRepository(uow.session)
            saved = await repository.get_for_origin(ctx.user_id, origin)
            if saved is None:
                return None, None
            return saved, await repository.reveal_secret(ctx.user_id, origin)
        finally:
            reset_current_context(token)


async def _record(
    ctx,
    *,
    origin: str,
    action: str,
    outcome: str,
    web_login_id=None,
    detail: str | None = None,
    mark_used: bool = False,
) -> None:
    """Append to the audit trail, on its own short-lived connection."""
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.web_login.contracts import WebLoginRepository

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        repository = WebLoginRepository(uow.session)
        await repository.record(
            user_id=ctx.user_id,
            origin=origin,
            action=action,
            outcome=outcome,
            web_login_id=web_login_id,
            conversation_id=ctx.conversation_id,
            actor=_actor(ctx),
            detail=detail,
        )
        if mark_used:
            await repository.mark_used(ctx.user_id, origin)
        await uow.commit()


def _actor(ctx) -> str | None:
    """Which agent did it, for the audit trail."""
    name = getattr(ctx, "agent_name", None)
    return f"agent:{name}" if name else None
