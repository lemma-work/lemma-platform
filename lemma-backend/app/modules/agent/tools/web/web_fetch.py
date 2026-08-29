"""Capture web pages into the agent's workspace.

The capability already existed — `/usr/local/bin/save-webpage` drives a headed
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
serialised on purpose: `start-browser` runs one shared session per sandbox (one
Xvfb display, one profile), so concurrent captures would fight over the same
page. A whole-batch deadline bounds the pathological case where most of the list
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
import shlex
from urllib.parse import urlparse

from app.core.log.log import get_logger
from app.core.net.impersonating_client import web_page_policy
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.web.models import (
    WebFetchPage,
    WebFetchRequest,
    WebFetchResponse,
)
from app.modules.agent.tools.web.page_extract import (
    ExtractedPage,
    PageFetchError,
    fetch_and_clean,
    render_document,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    get_workspace_session,
    workspace_runtime_context,
)

logger = get_logger(__name__)

# A plain fetch is quick; a browser render has to start Chrome and wait for the
# network to settle.
_HTTP_TIMEOUT_SECONDS = 45
_BROWSER_TIMEOUT_SECONDS = 75

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

# Browser renders are the serialised, expensive path, and the batch budget is
# the only thing bounding them.
#
# This is deliberately the same number as `WebFetchRequest.urls`' `max_length`,
# and `test_web_fetch_limits_agree` fails if the two ever drift: the cap is
# only honest when the tool cannot accept more pages than it will render. It
# used to accept ten and render three, so a caller who sent ten JS-heavy pages
# discovered seven were skipped only after paying for the call.
#
# Raising it does not endanger the cheap pages the old comment worried about --
# the http path runs to completion first, concurrently, before any render
# starts. What bounds the pathological case is the batch deadline, which
# reports every page it did not reach. Measured, a real render is ~5s, so five
# of them sit an order of magnitude inside that budget.
_MAX_BROWSER_RENDERS = 5

# Enough to saturate the network wait without opening twenty sockets to twenty
# sites at once.
_MAX_CONCURRENT_FETCHES = 5

_PREVIEW_CHARS = 400

# The real "this page needs a browser" signal is extraction returning nothing,
# which `fetch_and_clean` raises for. This floor only catches a degenerate
# extraction — a breadcrumb or a cookie notice and nothing else. It is
# deliberately low: plenty of real pages are short (example.com extracts to 167
# clean characters), and treating "short" as "broken" spends a browser render to
# re-fetch a page that was already read correctly.
_THIN_CONTENT_CHARS = 120

_ALLOWED_SCHEMES = {"http", "https"}


def _slugify(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or "page"
    slug = "".join(char if char.isalnum() or char in "-._" else "-" for char in raw)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:100].lower() or "page"


def _validate(url: str) -> str | None:
    """Reject anything that is not an ordinary web URL."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"Only http(s) URLs can be fetched (got '{parsed.scheme or 'none'}')."
    if not parsed.netloc:
        return "That URL has no host."
    return None


def _needs_browser(formats: list[str], render: bool) -> bool:
    # A PDF or screenshot is a render by definition; markdown alone is not.
    return render or any(fmt in {"pdf", "jpeg", "png"} for fmt in formats)


async def _write_markdown(session, *, path: str, content: str) -> None:
    """Write already-cleaned markdown into the workspace.

    Through the runtime's file API, not a shell heredoc. The heredoc could not
    work for the pages that most need capturing: it puts the whole document in a
    single `sh -c` argument, and Linux caps one argument at `MAX_ARG_STRLEN`
    (32 pages = 128KB) no matter how large `ARG_MAX` is. A full Wikipedia
    article extracts to ~185KB of markdown, so it failed with E2BIG — and the
    caller read that failure as "this page needs a browser" and spent a Chrome
    render re-fetching a page it had already read correctly.

    Streaming the bytes also removes the quoting question entirely: no shell
    parses this, so nothing on the page can terminate the document early.

    The directory is created once for the batch by the caller, not here: this
    runs under the write lock, once per page, against a directory that is the
    same for every page in the call. Five pages meant five `mkdir` round trips
    to create one directory — measured at ~80ms each, half of all the sandbox
    chatter in a batch.
    """
    await session.write_file(
        path, content.encode("utf-8"), timeout=_HTTP_TIMEOUT_SECONDS
    )


def _browser_script(url: str, out_dir: str, name: str, formats: list[str]) -> str:
    return (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"save-webpage {shlex.quote(url)} "
        f"--formats {shlex.quote(','.join(formats))} "
        f"--out {shlex.quote(out_dir)} --name {shlex.quote(name)}"
    )


def _expected_files(out_dir: str, name: str, formats: list[str]) -> dict[str, str]:
    suffix = {"markdown": "md", "pdf": "pdf", "jpeg": "jpg", "png": "png"}
    return {fmt: f"{out_dir}/{name}.{suffix[fmt]}" for fmt in formats if fmt in suffix}


async def web_fetch_internal(
    ctx: BaseAgentContext, request: WebFetchRequest
) -> WebFetchResponse:
    runtime_context = workspace_runtime_context(ctx)
    out_dir = request.out_dir.strip().strip("/") or "research"
    formats = list(dict.fromkeys(request.formats)) or ["markdown"]

    urls = list(dict.fromkeys(request.urls))
    pages: dict[str, WebFetchPage] = {}
    reached_deadline = False
    session_error: str | None = None

    try:
        # The deadline wraps acquiring the workspace as well as using it: a cold
        # sandbox has to be provisioned before the first capture, and that wait
        # is part of what the caller is sitting through.
        async with asyncio.timeout(_BATCH_BUDGET_SECONDS):
            session = await get_workspace_session(
                ctx,
                session_id=runtime_context.default_shell_session_id,
                close_on_exit=False,
            )
            async with session:
                await _capture_batch(
                    session,
                    urls=urls,
                    out_dir=out_dir,
                    formats=formats,
                    render=request.render,
                    pages=pages,
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
        out_dir=out_dir,
        pages=ordered,
        error=session_error if not captured else None,
        message=(
            f"Captured {captured} of {len(ordered)} page(s) into '{out_dir}'. "
            "Read them with `exec_command` (grep/cat), view screenshots with "
            "`view_image`."
            + (
                " The call hit its time budget before finishing; re-request the "
                "pages marked not attempted."
                if reached_deadline
                else ""
            )
            if captured
            else "No pages could be captured."
        ),
    )


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
    session,
    *,
    urls: list[str],
    out_dir: str,
    formats: list[str],
    render: bool,
    pages: dict[str, WebFetchPage],
) -> None:
    """Fill ``pages`` for every URL, cheap path first and in parallel.

    Written to mutate a dict the caller owns so that a batch cut short by the
    deadline above still returns everything captured before it.
    """
    needs_browser: list[str] = []

    await _reject_unsafe_urls(urls, pages)

    cheap = [
        url for url in urls if url not in pages and not _needs_browser(formats, render)
    ]
    if cheap:
        # Once for the batch, before any page is written. Every page in a call
        # shares `out_dir`, so doing this per page was the same directory
        # created five times over five serialised round trips.
        await session.exec_command(cmd=f"mkdir -p {shlex.quote(out_dir)}", timeout=20)
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
                page = await _clean_or_none(url)
            if page is None:
                needs_browser.append(url)
                return
            async with writing:
                written = await _write_extracted(
                    session, url=url, page=page, out_dir=out_dir
                )
            if written is None:
                needs_browser.append(url)
            else:
                pages[url] = written

        await asyncio.gather(*(_capture(url) for url in cheap))

    needs_browser.extend(
        url for url in urls if url not in pages and url not in needs_browser
    )

    for index, url in enumerate(needs_browser):
        if index >= _MAX_BROWSER_RENDERS:
            pages[url] = WebFetchPage(
                url=url,
                success=False,
                error=(
                    f"Skipped: this page needs the full browser, and a single "
                    f"call renders at most {_MAX_BROWSER_RENDERS} (the sandbox "
                    "has one browser, so they run one at a time). Ask for it "
                    "again on its own."
                ),
            )
            continue
        pages[url] = await _capture_with_browser(
            session, url=url, out_dir=out_dir, formats=formats
        )


async def _capture_with_browser(
    session,
    *,
    url: str,
    out_dir: str,
    formats: list[str],
) -> WebFetchPage:
    name = _slugify(url)
    browser_formats = formats if "markdown" in formats else [*formats, "markdown"]
    result = await session.exec_command(
        cmd=_browser_script(url, out_dir, name, browser_formats),
        timeout=_BROWSER_TIMEOUT_SECONDS,
    )
    return await _finish(
        session,
        url=url,
        out_dir=out_dir,
        name=name,
        formats=browser_formats,
        fetched_with="browser",
        failure_output=(result.get("stderr") or result.get("stdout") or ""),
    )


async def _present_files(session, paths: list[str]) -> dict[str, int]:
    """Of the captures we asked for, the ones that are really on disk.

    One command for the whole set, printing `size path` per non-empty file.
    """
    if not paths:
        return {}
    quoted = " ".join(shlex.quote(path) for path in paths)
    listing = await session.exec_command(
        cmd=(
            f'for f in {quoted}; do [ -s "$f" ] && '
            'printf "%s %s\\n" "$(wc -c < "$f" | tr -d " ")" "$f"; done'
        ),
        timeout=30,
    )
    sizes: dict[str, int] = {}
    for line in (listing.get("stdout") or "").splitlines():
        size, _, path = line.strip().partition(" ")
        if path and size.isdigit():
            sizes[path] = int(size)
    return sizes


async def _finish(
    session,
    *,
    url: str,
    out_dir: str,
    name: str,
    formats: list[str],
    fetched_with: str,
    failure_output: str,
) -> WebFetchPage:
    """Report what the browser actually produced, not what was requested.

    The result used to be the *expected* paths plus `success=True` whenever the
    capture command did not exit non-zero — so a page the browser could not
    render (Britannica refuses ours) came back as a success naming a file that
    was never written, and the agent went looking for it. Exit codes were the
    wrong thing to trust anyway: a render outliving its wait window reports no
    exit code at all, which read as success. What is on disk is the answer.
    """
    expected = _expected_files(out_dir, name, formats)
    present = await _present_files(session, list(expected.values()))
    files = {fmt: path for fmt, path in expected.items() if path in present}

    markdown_path = files.get("markdown")
    if markdown_path is None:
        return WebFetchPage(
            url=url,
            success=False,
            fetched_with=fetched_with,
            error=(
                failure_output.strip()[:400]
                or "The browser produced no readable article for this page. "
                "Some sites refuse automated clients outright."
            ),
        )

    head = await session.exec_command(
        cmd=f"head -c {_PREVIEW_CHARS * 2} {shlex.quote(markdown_path)}",
        timeout=20,
    )
    preview = None
    title = None
    text = (head.get("stdout") or "").strip()
    if text:
        title = text.splitlines()[0].lstrip("# ").strip() or None
        preview = text[:_PREVIEW_CHARS]

    return WebFetchPage(
        url=url,
        success=True,
        title=title,
        files=files,
        preview=preview,
        characters=present[markdown_path],
        fetched_with=fetched_with,
    )


async def _clean_or_none(url: str) -> ExtractedPage | None:
    """Fetch and clean in-process, or None when the browser is needed.

    Cheap path: no browser start-up and no container round-trip to fetch, so raw
    HTML never leaves this process. Touches no workspace state, which is what
    makes it safe to run for several URLs at once.
    """
    try:
        page = await fetch_and_clean(url)
    except PageFetchError as exc:
        # Most often "renders with JavaScript", sometimes a 403 at a site that
        # refuses scripted clients. Escalate rather than reporting an empty
        # article as a success.
        logger.warning(
            "agent.web_fetch.http_path_failed.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None
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
    if len(page.markdown) < _THIN_CONTENT_CHARS:
        return None
    return page


async def _write_extracted(
    session, *, url: str, page: ExtractedPage, out_dir: str
) -> WebFetchPage | None:
    """Save a cleaned page, or None when the write itself failed."""
    document = render_document(page)
    markdown_path = f"{out_dir}/{_slugify(url)}.md"
    try:
        await _write_markdown(session, path=markdown_path, content=document)
    except Exception:  # noqa: BLE001 - falls back to the browser path
        # Logged rather than swallowed: the caller will spend a browser render
        # recovering from this, and a recurring write failure is a workspace
        # problem, not a property of the page.
        logger.warning(
            "agent.web_fetch.workspace_write_failed.degraded",
            characters=len(document),
            exc_info=True,
        )
        return None

    return WebFetchPage(
        url=url,
        success=True,
        title=page.title,
        files={"markdown": markdown_path},
        preview=document[:_PREVIEW_CHARS],
        characters=len(document),
        fetched_with="http",
    )
