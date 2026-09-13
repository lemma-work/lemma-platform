"""Getting the agent past a login wall by asking the person.

The shape of this tool is the whole point. It does not hand the model a link and
tell it to wait -- that is what the first version did, and nothing resumed the
run, so the agent's only option was to end its turn and hope. Here the tool
*pauses*, exactly the way `ask_user` and `request_approval` do: the call is
persisted, the conversation goes to WAITING, and the person's answer starts a
fresh run that replays this call's return.

That also means the ask reaches the person wherever they are, with no work here:
a waiting conversation already renders as a card in the web app and as native
buttons on WhatsApp, Slack, Telegram and email.

The model never sees a password and is never asked to type one. It names a site;
the person signs in themselves in the browser; what comes back is whether that
worked.
"""

from __future__ import annotations

from app.modules.agent.tools.browser.models import (
    BrowserSignInRequest,
    BrowserSignInResponse,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_errors import AgentInputRequired
from app.modules.web_login.contracts import InvalidOrigin, normalize_origin

SIGN_IN_TOOL_NAME = "browser_sign_in"


async def sign_in_internal(
    deps: BaseAgentContext,
    request: BrowserSignInRequest,
    *,
    tool_call_id: str | None = None,
) -> BrowserSignInResponse:
    """Try a saved login; ask the person only if there is not a working one."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.web_login.contracts import SignInService

    try:
        site = normalize_origin(request.origin)
    except InvalidOrigin as exc:
        return BrowserSignInResponse(
            success=False,
            outcome="error",
            origin=request.origin,
            message=str(exc),
        )

    service = SignInService(get_uow_factory())
    try:
        loaded, detail = await service.try_saved_login(origin=site)
        if loaded:
            return BrowserSignInResponse(
                success=True,
                outcome="signed_in",
                source="saved",
                origin=site,
                message=(
                    f"{detail}. Open the page again -- it should not ask now. "
                    "If it still shows a login, call this again and say so in "
                    "`reason`, and the person will be asked."
                ),
            )

        if not tool_call_id:
            # Without a durable call id there is nothing for an answer to
            # resolve against, so pausing would strand the person's decision.
            return BrowserSignInResponse(
                success=False,
                outcome="error",
                origin=site,
                message="signing in needs a durable tool call id",
            )

        await service.open_request(
            origin=site,
            reason=request.reason,
            conversation_id=deps.conversation_id,
            tool_call_id=tool_call_id,
        )
    finally:
        await service.close()

    # Ends the run cleanly. The person's answer -- in the app, or from a button
    # on whatever surface reached them -- starts a fresh run that replays this
    # call with a real outcome in place of this raise.
    raise AgentInputRequired(tool_call_id, SIGN_IN_TOOL_NAME)


__all__ = ["SIGN_IN_TOOL_NAME", "sign_in_internal"]
