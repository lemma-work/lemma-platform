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

The browser path is serialised on purpose: `start-browser` runs one shared
session per sandbox (one Xvfb display, one profile), so concurrent captures
would fight over the same page.
"""

from __future__ import annotations

import shlex
from urllib.parse import urlparse

from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.web.models import (
    WebFetchPage,
    WebFetchRequest,
    WebFetchResponse,
)
from app.modules.agent.tools.web.page_extract import (
    PageFetchError,
    fetch_and_clean,
    render_document,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    _get_workspace_session,
    workspace_runtime_context,
)

logger = get_logger(__name__)

# A plain fetch is quick; a browser render has to start Chrome and wait for the
# network to settle.
_HTTP_TIMEOUT_SECONDS = 45
_BROWSER_TIMEOUT_SECONDS = 120

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


def _write_script(out_dir: str, path: str, content: str) -> str:
    """Write already-cleaned markdown into the workspace.

    A heredoc with a quoted delimiter, so the document is written literally —
    no shell expansion of whatever happened to be on the page.
    """
    return (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"cat > {shlex.quote(path)} <<'LEMMA_WEB_FETCH_EOF'\n"
        f"{content}\n"
        "LEMMA_WEB_FETCH_EOF"
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
    return {
        fmt: f"{out_dir}/{name}.{suffix[fmt]}" for fmt in formats if fmt in suffix
    }


async def web_fetch_internal(
    ctx: BaseAgentContext, request: WebFetchRequest
) -> WebFetchResponse:
    runtime_context = workspace_runtime_context(ctx)
    out_dir = request.out_dir.strip().strip("/") or "research"
    formats = list(dict.fromkeys(request.formats)) or ["markdown"]

    pages: list[WebFetchPage] = []
    try:
        session = await _get_workspace_session(
            ctx,
            session_id=runtime_context.default_shell_session_id,
            close_on_exit=False,
        )
        async with session:
            for url in request.urls:
                pages.append(
                    await _fetch_one(
                        session,
                        url=url,
                        out_dir=out_dir,
                        formats=formats,
                        render=request.render,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - graceful tool boundary
        logger.debug("agent.web_fetch.session_failed.diagnostic", exc_info=True)
        return WebFetchResponse(
            success=False,
            error=(
                f"Could not reach the workspace to capture pages: "
                f"{type(exc).__name__}. Retry if the pages are still needed."
            ),
            pages=pages,
        )

    captured = sum(1 for page in pages if page.success)
    return WebFetchResponse(
        # Partial success is still success: one dead link must not discard the
        # nine pages that came back.
        success=captured > 0,
        out_dir=out_dir,
        pages=pages,
        message=(
            f"Captured {captured} of {len(pages)} page(s) into '{out_dir}'. "
            "Read them with `exec_command` (grep/cat), view screenshots with "
            "`view_image`."
            if captured
            else "No pages could be captured."
        ),
    )


async def _fetch_one(
    session,
    *,
    url: str,
    out_dir: str,
    formats: list[str],
    render: bool,
) -> WebFetchPage:
    invalid = _validate(url)
    if invalid:
        return WebFetchPage(url=url, success=False, error=invalid)

    name = _slugify(url)

    if not _needs_browser(formats, render):
        # Returns None when the page needs a real browser after all — a fetch
        # that failed, or one that came back with no readable article.
        captured = await _fetch_without_browser(
            session, url=url, out_dir=out_dir, name=name
        )
        if captured is not None:
            return captured

    browser_formats = formats if "markdown" in formats else [*formats, "markdown"]
    result = await session.exec_command(
        cmd=_browser_script(url, out_dir, name, browser_formats),
        timeout=_BROWSER_TIMEOUT_SECONDS,
    )
    if result.get("exit_code") not in (0, None):
        return WebFetchPage(
            url=url,
            success=False,
            fetched_with="browser",
            error=(
                (result.get("stderr") or result.get("stdout") or "").strip()[:400]
                or "The page could not be captured."
            ),
        )
    return await _finish(
        session,
        url=url,
        out_dir=out_dir,
        name=name,
        formats=browser_formats,
        fetched_with="browser",
        characters=None,
    )


async def _finish(
    session,
    *,
    url: str,
    out_dir: str,
    name: str,
    formats: list[str],
    fetched_with: str,
    characters: int | None,
) -> WebFetchPage:
    files = _expected_files(out_dir, name, formats)
    preview = None
    title = None
    markdown_path = files.get("markdown")
    if markdown_path:
        head = await session.exec_command(
            cmd=f"head -c {_PREVIEW_CHARS * 2} {shlex.quote(markdown_path)}",
            timeout=20,
        )
        text = (head.get("stdout") or "").strip()
        if text:
            first_line = text.splitlines()[0].lstrip("# ").strip()
            title = first_line or None
            preview = text[:_PREVIEW_CHARS]
        if characters is None:
            size = await session.exec_command(
                cmd=f"wc -c < {shlex.quote(markdown_path)}", timeout=20
            )
            characters = _parse_int(size.get("stdout"))

    return WebFetchPage(
        url=url,
        success=True,
        title=title,
        files=files,
        preview=preview,
        characters=characters,
        fetched_with=fetched_with,
    )


def _parse_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


async def _fetch_without_browser(
    session, *, url: str, out_dir: str, name: str
) -> WebFetchPage | None:
    """Fetch and clean in-process, or None if the browser is needed.

    Cheap path: no browser start-up and no container round-trip to fetch — the
    sandbox is touched only to write the finished markdown, so raw HTML never
    leaves this process.
    """
    try:
        page = await fetch_and_clean(url)
    except PageFetchError as exc:
        # Most often "renders with JavaScript". Escalate rather than reporting
        # an empty article as a success.
        logger.debug(
            "agent.web_fetch.http_path_failed.diagnostic",
            error_type=type(exc).__name__,
        )
        return None

    document = render_document(page)
    # Measured on the extracted article, not the rendered document — the
    # provenance header would otherwise mask an empty extraction.
    if len(page.markdown) < _THIN_CONTENT_CHARS:
        return None

    markdown_path = f"{out_dir}/{name}.md"
    written = await session.exec_command(
        cmd=_write_script(out_dir, markdown_path, document),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    if written.get("exit_code") != 0:
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
