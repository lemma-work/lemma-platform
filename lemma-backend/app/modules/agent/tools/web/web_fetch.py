"""Capture web pages into the agent's workspace.

The capability already existed — `save-webpage` drives a headed
Chrome in the sandbox and emits Readability→Turndown markdown, a PDF, and
full-page screenshots — but it was reachable only by `exec_command` and
advertised in ten lines of prose, one URL at a time. Research means reading ten
sources, so it is a tool now, and it takes a list.

Two paths, because they cost very differently:

* **http** (default) — fetched and reduced to clean markdown in this process by
  `page_extract`, then written to the workspace. No browser start-up and no
  container round-trip to fetch, so it is fast; and because boilerplate is
  stripped before anything is stored, raw HTML never reaches the agent.
* **browser** — `save-webpage` in the sandbox, for pages that only exist after
  JavaScript runs and for any request that wants a PDF or screenshot.

Both write into the workspace and return *paths plus a preview*, never the page
text. That is the whole point: twenty pages of article text would swamp the
context this tool exists to protect. The agent greps what it saved, `view_image`s
a screenshot, or runs `pod_view_document_pages` over a captured PDF.

Fetches on the http path run concurrently — they are independent network waits,
and a research batch is the case this tool was built for. The browser path stays
serialised on purpose, and that was re-measured rather than inherited. Every
`agent-browser` command acts on its session's *active* tab -- `--session` can
be targeted, a tab cannot -- so two captures sharing a session read each
other's page. Giving each its own session sidesteps that and costs a cold
Chrome apiece: measured on 1 vCPU / 2 GB, three pages, byte-identical
markdown either way, serial 11.0s against parallel 75.4s.

Memory is not the constraint, whatever the RSS sums elsewhere suggest: five
concurrent tabs in one browser peaked at 1656 MiB of a 2048 MiB cgroup. The
one vCPU is.

A whole-batch deadline bounds the pathological case where most of the list
needs rendering.

Every URL is checked against `assert_safe_url` before *either* path runs. The
in-process fetch reaches the network from the backend, and the browser reaches
it from inside the sandbox, so a check on only one of them would leave the other
as a way to the metadata service. What makes the cheap path win more often is
`impersonating_client`, which wears a real Chrome TLS fingerprint — sites decide
during the handshake, long before they read a `User-Agent`.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
import shlex
from urllib.parse import urlparse

from app.core.log.log import get_logger
from app.core.net.impersonating_client import web_page_policy
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.tools.tool_payload_limits import bounded_tool_text
from app.modules.agent.tools.web.browser_capture import (
    MAX_BROWSER_RENDERS,
    capture_with_browser,
    needs_browser,
    render_fits,
)
from app.modules.agent.tools.web.capture_result import (
    THIN_CONTENT_CHARS as _THIN_CONTENT_CHARS,
)
from app.modules.agent.tools.web.http_capture import (
    Refusal,
    inline_page,
    refusal_for,
    write_extracted,
)
from app.modules.agent.tools.web.models import (
    WebFetchPage,
    WebFetchRequest,
    WebFetchResponse,
)
from app.modules.agent.tools.web.page_extract import (
    ExtractedPage,
    PageFetchError,
    fetch_and_clean,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    get_workspace_session,
    workspace_runtime_context,
)

logger = get_logger(__name__)

# The batch as a whole, and a hard one: the tool returns at this point whatever
# it has, because nothing above it will stop it.
#
# There is no per-tool timeout anywhere in the harness. A tool that blocks blocks
# its run until the streaq job ceiling (55 minutes) kills the worker task — and
# that path records the job as *succeeded*, so the user is left with a run that
# simply stopped. A research batch is exactly the shape that gets there: four
# URLs of which three need the browser, serialised because the sandbox has one
# shared browser session, is already minutes of work before anything goes wrong.
#
# So the budget is enforced with a real deadline rather than checked between
# steps, and partial results survive it. An agent that gets three of four pages
# and a note about the fourth can act; an agent whose run disappears cannot.
_BATCH_BUDGET_SECONDS = 240

# Enough to saturate the network wait without opening twenty sockets to twenty
# sites at once.
_MAX_CONCURRENT_FETCHES = 5

# The text handed back inline, for an agent with no tool that can read a saved
# file, shared across the pages of one call -- and never less than a useful
# share per page. Bounded because every inline character is replayed with the
# rest of the conversation on every later step.
_INLINE_BUDGET_CHARS = 40_000
_INLINE_PAGE_FLOOR_CHARS = 8_000


_ALLOWED_SCHEMES = {"http", "https"}


def _validate(url: str) -> str | None:
    """Reject anything that is not an ordinary web URL."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"Only http(s) URLs can be fetched (got '{parsed.scheme or 'none'}')."
    if not parsed.netloc:
        return "That URL has no host."
    return None


class _Workspace:
    """The sandbox session, opened the first time a page needs it.

    An agent with no tool that can read a saved file gets its pages inline, and
    a batch the cheap path answers then never needs a sandbox at all --
    provisioning one cost the wait for a sandbox to hand back text the backend
    already held.
    """

    def __init__(self, ctx: ToolContext, stack: AsyncExitStack) -> None:
        self._ctx = ctx
        self._stack = stack
        self._session = None
        self._opening = asyncio.Lock()

    async def session(self):
        async with self._opening:
            if self._session is None:
                runtime_context = workspace_runtime_context(self._ctx)
                opened = await get_workspace_session(
                    self._ctx,
                    session_id=runtime_context.default_shell_session_id,
                    close_on_exit=False,
                )
                self._session = await self._stack.enter_async_context(opened)
        return self._session


@dataclass(frozen=True, slots=True)
class _Batch:
    """What every page in one call shares."""

    out_dir: str
    formats: list[str]
    render: bool
    #: Pages come back in the result rather than as files to read.
    inline: bool
    #: Characters of each page's text handed back inline.
    inline_limit: int
    #: When the call's budget runs out, on the event loop's clock.
    deadline: float


async def web_fetch_internal(
    ctx: ToolContext, request: WebFetchRequest
) -> WebFetchResponse:
    urls = list(dict.fromkeys(request.urls))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BATCH_BUDGET_SECONDS
    batch = _Batch(
        out_dir=request.out_dir.strip().strip("/") or "research",
        formats=list(dict.fromkeys(request.formats)) or ["markdown"],
        render=request.render,
        inline=not ctx.workspace_readable,
        inline_limit=max(
            _INLINE_PAGE_FLOOR_CHARS, _INLINE_BUDGET_CHARS // max(1, len(urls))
        ),
        deadline=deadline,
    )
    pages: dict[str, WebFetchPage] = {}
    reached_deadline = False
    session_error: str | None = None

    try:
        # The deadline wraps acquiring the workspace as well as using it: a cold
        # sandbox has to be provisioned before the first capture, and that wait
        # is part of what the caller is sitting through.
        async with asyncio.timeout_at(deadline), AsyncExitStack() as stack:
            await _capture_batch(
                _Workspace(ctx, stack), urls=urls, batch=batch, pages=pages
            )
    except TimeoutError:
        # Not an error: the tool ran out of its own budget. Everything captured
        # before now is still on disk and still worth returning.
        reached_deadline = True
        logger.warning(
            "agent.web_fetch.batch_deadline_reached.degraded",
            requested=len(urls),
            captured=sum(1 for page in pages.values() if page.success),
        )
    except Exception as exc:  # noqa: BLE001 - graceful tool boundary
        logger.warning(
            "agent.web_fetch.session_failed.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        session_error = (
            f"Could not reach the workspace to capture pages: "
            f"{type(exc).__name__}. Retry if the pages are still needed."
        )

    for url in urls:
        pages.setdefault(
            url,
            WebFetchPage(
                url=url,
                success=False,
                error=(
                    "Not attempted: this call reached its time budget first. "
                    "Ask for this page again on its own."
                    if reached_deadline
                    else session_error
                    or "Not attempted: the batch ended before reaching this page."
                ),
            ),
        )

    ordered = [pages[url] for url in urls]
    captured = sum(1 for page in ordered if page.success)
    return WebFetchResponse(
        # Partial success is still success: one dead link must not discard the
        # nine pages that came back, and neither must running out of time.
        success=captured > 0,
        out_dir=None if batch.inline else batch.out_dir,
        pages=ordered,
        error=session_error if not captured else None,
        message=_message(
            batch, captured=captured, total=len(ordered), cut_short=reached_deadline
        ),
    )


def _message(batch: _Batch, *, captured: int, total: int, cut_short: bool) -> str:
    """What to do with the result, in terms of the tools this agent has.

    It used to say "read them with `exec_command`" to every agent, including
    the ones web_fetch is given by default and `exec_command` is not, which
    could then see only a preview of each page and fetched it again.
    """
    if not captured:
        return "No pages could be captured."
    if batch.inline:
        message = (
            f"Fetched {captured} of {total} page(s). Each page's text is in its "
            f"`content`, clipped at {batch.inline_limit} characters."
        )
        if any(fmt != "markdown" for fmt in batch.formats):
            message += (
                " Screenshots and PDFs were saved in the sandbox at the paths "
                "given, but you have no tool that can open them."
            )
    else:
        message = (
            f"Captured {captured} of {total} page(s) into '{batch.out_dir}'. "
            "Read them with `exec_command` (grep/cat), view screenshots with "
            "`view_image`."
        )
    if cut_short:
        message += (
            " The call hit its time budget before finishing; re-request the "
            "pages marked not attempted."
        )
    return message


async def _reject_unsafe_urls(urls: list[str], pages: dict[str, WebFetchPage]) -> None:
    """Record a failure for every URL we are not willing to request at all.

    Runs before either path, which is the point: `save-webpage` executes inside
    the sandbox, where an internal address is *more* reachable than it is from
    here, not less. Guarding only the in-process fetch would have left the
    browser as an unguarded way to the same address.

    The cheap shape check runs first so a typo is answered without a DNS lookup,
    and `assert_safe_url` — the same validator the connector paths use — does
    the rest. Checks run concurrently: each is a resolver round trip.
    """

    async def _check(url: str) -> None:
        malformed = _validate(url)
        if malformed:
            pages[url] = WebFetchPage(url=url, success=False, error=malformed)
            return
        try:
            await assert_safe_url(url, policy=web_page_policy())
        except UnsafeUrlError as exc:
            # The reason, never the address: which internal range answered is
            # infrastructure detail about wherever this is deployed.
            logger.warning("agent.web_fetch.url_refused.refused", reason=exc.reason)
            pages[url] = WebFetchPage(
                url=url,
                success=False,
                error=f"That URL is not a permitted fetch target ({exc.reason}).",
            )

    await asyncio.gather(*(_check(url) for url in urls))


async def _capture_batch(
    workspace: _Workspace,
    *,
    urls: list[str],
    batch: _Batch,
    pages: dict[str, WebFetchPage],
) -> None:
    """Fill ``pages`` for every URL, cheap path first and in parallel.

    Written to mutate a dict the caller owns so that a batch cut short by the
    deadline above still returns everything captured before it.
    """
    await _reject_unsafe_urls(urls, pages)

    to_render: list[str] = []
    cheap = [
        url
        for url in urls
        if url not in pages and not needs_browser(batch.formats, batch.render)
    ]
    if cheap:
        await _capture_cheap(
            workspace, cheap, batch=batch, pages=pages, to_render=to_render
        )
    to_render.extend(url for url in urls if url not in pages and url not in to_render)

    loop = asyncio.get_running_loop()
    for index, url in enumerate(to_render):
        if index >= MAX_BROWSER_RENDERS:
            pages[url] = WebFetchPage(
                url=url,
                success=False,
                error=(
                    f"Skipped: this page needs the full browser, and a single "
                    f"call renders at most {MAX_BROWSER_RENDERS} (the sandbox "
                    "has one browser, so they run one at a time). Ask for it "
                    "again on its own."
                ),
            )
            continue
        if not render_fits(batch.deadline - loop.time()):
            # Not started rather than cut off: a render begun with too little
            # time left is abandoned mid-flight by the deadline, and the agent
            # learns nothing it would not learn from asking again.
            pages[url] = WebFetchPage(
                url=url,
                success=False,
                error=(
                    "Not attempted: this page needs the full browser and too "
                    "little of this call's time was left to render it. Ask for "
                    "it again on its own."
                ),
            )
            continue
        session = await workspace.session()
        page = await capture_with_browser(
            session, url=url, out_dir=batch.out_dir, formats=batch.formats
        )
        pages[url] = (
            await _with_saved_text(session, page, limit=batch.inline_limit)
            if batch.inline
            else page
        )


async def _capture_cheap(
    workspace: _Workspace,
    urls: list[str],
    *,
    batch: _Batch,
    pages: dict[str, WebFetchPage],
    to_render: list[str],
) -> None:
    """The http path for ``urls``, concurrently; what it cannot read joins
    ``to_render``."""
    if not batch.inline:
        # Once for the batch, before any page is written. Every page in a call
        # shares `out_dir`, so doing this per page was the same directory
        # created five times over five serialised round trips.
        session = await workspace.session()
        await session.exec_command(
            cmd=f"mkdir -p {shlex.quote(batch.out_dir)}", timeout=20
        )
    # Binds only if the URL cap is ever raised above it -- `WebFetchRequest`
    # accepts five and this allows five. Kept because the cap is the thing
    # that moves, and an unbounded gather over a longer list is how you open
    # twenty sockets at once.
    limit = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
    # Writes share one workspace session, so they are serialised even though
    # the fetches are not.
    writing = asyncio.Lock()

    async def _capture(url: str) -> None:
        """Fetch, then save — and record the result the moment it exists.

        Deliberately not `gather`-then-write: collecting every extraction
        before writing any makes the batch all-or-nothing, so one slow site
        costs the caller every fast page that had already come back when the
        deadline fired. Recording per URL is what makes a partial result
        actually partial.
        """
        async with limit:
            page = await _clean_or_none(url, can_download=not batch.inline)
        if page is None:
            to_render.append(url)
            return
        if isinstance(page, Refusal):
            pages[url] = WebFetchPage(
                url=url,
                success=False,
                error=page.message,
                fetched_with="http",
                status=page.status,
                blocked_by=page.blocked_by,
            )
            return
        if batch.inline:
            pages[url] = inline_page(url, page, limit=batch.inline_limit)
            return
        async with writing:
            written = await write_extracted(
                await workspace.session(), url=url, page=page, out_dir=batch.out_dir
            )
        if written is None:
            to_render.append(url)
        else:
            pages[url] = written

    await asyncio.gather(*(_capture(url) for url in urls))


async def _with_saved_text(session, page: WebFetchPage, *, limit: int) -> WebFetchPage:
    """A rendered page with its text attached, for an agent that cannot read
    the file it was saved to."""
    path = page.files.get("markdown")
    if not page.success or path is None:
        return page
    # Bytes, not characters; four per character covers any UTF-8 text.
    read = await session.exec_command(
        cmd=f"head -c {limit * 4} {shlex.quote(path)}", timeout=20
    )
    text = str(read.get("stdout") or "")
    return page.model_copy(
        update={"content": bounded_tool_text(text, limit=limit, what="page")}
    )


async def _clean_or_none(
    url: str, *, can_download: bool
) -> ExtractedPage | Refusal | None:
    """Fetch and clean in-process. `None` means "try the browser".

    Cheap path: no browser start-up and no container round-trip to fetch, so raw
    HTML never leaves this process. Touches no workspace state, which is what
    makes it safe to run for several URLs at once.
    """
    try:
        page = await fetch_and_clean(url)
    except PageFetchError as exc:
        # Three different things wearing one costume until now: a page that
        # renders with JavaScript, a site that refused us, and a page that is
        # simply not there. Only the first is worth a browser.
        logger.warning(
            "agent.web_fetch.http_path_failed.degraded",
            error_type=type(exc).__name__,
            status=exc.status,
            vendor=exc.verdict.vendor if exc.verdict else None,
            signal=exc.verdict.signal if exc.verdict else None,
            exc_info=True,
        )
        return refusal_for(url, exc, can_download=can_download)
    except Exception as exc:  # noqa: BLE001 - one URL must not sink the batch
        # These run under `gather`, so anything unhandled here fails every other
        # page in the call too. A malformed charset or an extractor crash is one
        # bad source, not a broken research batch — escalate this URL alone.
        logger.warning(
            "agent.web_fetch.http_path_crashed.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None

    # Measured on the extracted article, not the rendered document — the
    # provenance header would otherwise mask an empty extraction.
    #
    # This is an emptiness test and no longer doubles as a block test: a
    # 120-character floor flagged a genuine 117-character answer and missed
    # a 3,670-character block page. `classify` ran before this and owns that
    # question; what reaches here is "there was nothing to read", which a
    # browser genuinely can fix.
    if not page.raw and len(page.markdown) < _THIN_CONTENT_CHARS:
        return None
    return page
