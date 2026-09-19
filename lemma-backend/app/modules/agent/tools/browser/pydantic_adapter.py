from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.browser import sign_in
from app.modules.agent.tools.browser.models import (
    BrowserSignInRequest,
    BrowserSignInResponse,
)
from app.modules.agent.tools.context import BaseAgentContext


async def browser_sign_in(
    ctx: RunContext[BaseAgentContext],
    request: BrowserSignInRequest,
) -> BrowserSignInResponse:
    """
    Get signed in to a site, without ever seeing or asking for a password.

    Call this the moment a page needs a login — before touching a login form,
    and instead of asking anyone for credentials.

    If a saved login for the site exists it is loaded and you get `signed_in`
    straight away; open the page again and carry on. Otherwise this run pauses
    while the person signs in themselves, in this browser, wherever they are.
    You will be started again with the outcome once they have.

    `declined` means they said no: do the task another way, or stop and say
    plainly what you could not reach. Never ask a person to type a password
    into the conversation, never put one in a command, and never try to sign in
    by filling a form with credentials somebody sent you.
    """
    return await sign_in.sign_in_internal(
        ctx.deps, request, tool_call_id=ctx.tool_call_id
    )


BROWSER_TOOLS = [browser_sign_in]

# The browsing itself is not here. It is `agent-browser` in the workspace
# shell, reached through `exec_command` like every other command line, with
# `view_image` to look at what it captures -- and that is the whole surface.
#
# There were five typed tools beside this one (`browser_open`,
# `browser_snapshot`, `browser_act`, `browser_read`, `browser_screenshot`).
# Each was a wrapper that built one `agent-browser` command, ran it through the
# same shell session the agent can reach itself, and parsed the same JSON back
# -- so the agent had two ways to do one thing, had to be told in the skill
# which to prefer, and paid a round trip per step where the CLI chains with
# `&&`. `browser_screenshot` was the clearest case: it shelled out to
# `agent-browser screenshot`, read the file back and handed it to
# `downscale_for_vision` and the vision delegate -- which is exactly what
# `view_image` already does for any image on disk.
#
# What did not survive the wrappers had to move rather than be dropped: the
# human-takeover lease. It was enforced only in the script those tools built,
# so a raw `agent-browser` call -- the thing the skill itself taught -- typed
# straight over somebody signing in. It now lives in the `lemma-node-tool`
# wrapper, which is where every browser command enters regardless of who asked
# for it.
#
# Its own toolset rather than more entries in WORKSPACE_CLI, because the
# dependency only runs one way. It ships in `POD_DEFAULT_AGENT_TOOLSETS` so the
# default assistant can ask for a login.
browser_toolset = FunctionToolset[BaseAgentContext](tools=list(BROWSER_TOOLS))
