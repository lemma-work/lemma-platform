from __future__ import annotations

from pydantic_ai import ToolReturn
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.browser import browser, login
from app.modules.agent.tools.browser.login import (
    BrowserLoginRequest,
    BrowserLoginResult,
)
from app.modules.agent.tools.browser.models import (
    BrowserActRequest,
    BrowserOpenRequest,
    BrowserReadRequest,
    BrowserResult,
    BrowserScreenshotRequest,
    BrowserScreenshotResponse,
    BrowserSnapshotRequest,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ViewImageResponse


async def browser_open(
    ctx: RunContext[BaseAgentContext],
    request: BrowserOpenRequest,
) -> BrowserResult:
    """
    Open a page in the workspace's real Chromium and return what is on it.

    Use it for anything a page renders: JS-heavy sites, logins, forms, scraping,
    and apps running inside this sandbox (`http://127.0.0.1:<port>` — never a
    public preview URL). The browser starts itself on the first call.

    The result carries the page's `@eN` element refs. Act on them with
    `browser_act`, read from them with `browser_read`, and look at the page with
    `browser_screenshot`.

    When opening triggers a redirect, name where you expect to land with
    `wait_for_url` rather than opening and hoping — a snapshot taken mid-redirect
    describes a page that is already gone.
    """
    return await browser.open_internal(ctx.deps, request)


async def browser_snapshot(
    ctx: RunContext[BaseAgentContext],
    request: BrowserSnapshotRequest,
) -> BrowserResult:
    """
    Re-read the current page's elements and their `@eN` refs.

    `browser_open` and `browser_act` already return a fresh snapshot, so reach
    for this one when you need the page again without acting on it — after a
    background update, or to widen from interactive-only to the full tree.
    """
    return await browser.snapshot_internal(ctx.deps, request)


async def browser_act(
    ctx: RunContext[BaseAgentContext],
    request: BrowserActRequest,
) -> BrowserResult:
    """
    Click, fill, press, select or scroll, then return the page as it now is.

    Address elements by the `@eN` ref from the most recent result. **Refs go
    stale the moment the page changes**, which is why every call returns a new
    snapshot — use those refs for the next step, never ones from an earlier
    result.

    When the action navigates or submits, say what to wait for with
    `wait_for_url` or `wait_for_text`. Without it the snapshot can describe the
    old page.

    If a click seems to do nothing, a modal or cookie banner is usually
    intercepting it — find it in the snapshot and dismiss it first.
    """
    return await browser.act_internal(ctx.deps, request)


async def browser_read(
    ctx: RunContext[BaseAgentContext],
    request: BrowserReadRequest,
) -> BrowserResult:
    """
    Read text, an attribute, the URL, the title, or the console and network logs.

    Use `console` and `network` when a page misbehaves — a blank screen is
    usually a failed request or a thrown error, and stopping at the visual
    failure means guessing at it.
    """
    return await browser.read_internal(ctx.deps, request)


async def browser_screenshot(
    ctx: RunContext[BaseAgentContext],
    request: BrowserScreenshotRequest,
) -> BrowserScreenshotResponse | ViewImageResponse | ToolReturn:
    """
    Capture the page as an image and return it as binary tool content.

    Use it to see what a snapshot cannot describe: layout, charts, broken
    styles, error overlays. `annotate` draws numbered labels matching the `@eN`
    refs, which is how you tell two identical-looking buttons apart.

    Always set `instructions` — if this agent's model cannot see images, that is
    the question a vision model answers on your behalf.
    """
    return await browser.screenshot_internal(ctx.deps, request)


async def browser_login(
    ctx: RunContext[BaseAgentContext],
    request: BrowserLoginRequest,
) -> BrowserLoginResult:
    """
    Get signed in to a site, without ever handling the person's password.

    Call it when a page turns out to need a login — before filling a login form
    yourself, and instead of asking anybody for a password. You never see the
    credential either way.

    Three things can come back:

    - `signed_in: true` — a saved session was loaded. Open the page and check it
      took before going on.
    - `use_connector_instead` — this site is already connected properly. Use that
      connector; do not drive its login form.
    - `needs_person: true` with a `takeover_url` — nobody is signed in yet. Send
      the person that link, say what you were doing, and wait. It opens the very
      browser you are using so they can sign in themselves, and it only opens for
      them. Once they are done, call this again.

    Never ask anyone to type a password to you, and never put one in a command.
    """
    return await login.login_internal(ctx.deps, request)


BROWSER_TOOLS = [
    browser_open,
    browser_snapshot,
    browser_act,
    browser_read,
    browser_screenshot,
    browser_login,
]

# Its own toolset rather than more entries in WORKSPACE_CLI, because the
# dependency only runs one way: these tools are built on the workspace session
# helpers, so workspace_cli must not import them back. It ships in
# `POD_DEFAULT_AGENT_TOOLSETS` so the default assistant still gets the typed
# path rather than being left with the shell one.
browser_toolset = FunctionToolset[BaseAgentContext](tools=list(BROWSER_TOOLS))
