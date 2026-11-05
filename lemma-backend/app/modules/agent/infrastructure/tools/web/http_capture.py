"""The http path of `web_fetch`: what a fetched page becomes.

Split from `web_fetch` by use case. The fetch itself, and the decision to fall
back to the browser, stay in `web_fetch`; this is what happens to a page once
it has been fetched -- saved, refused with a reason, or annotated.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.tools.payload_limits import bounded_tool_text
from app.modules.agent.infrastructure.tools.web.capture_result import (
    PREVIEW_CHARS,
    slugify,
)
from app.modules.agent.infrastructure.tools.web.models import WebFetchPage
from app.modules.agent.infrastructure.tools.web.page_extract import (
    ExtractedPage,
    NotAWebPageError,
    PageFetchError,
    PageUnreachableFetchError,
    render_document,
)

logger = get_logger(__name__)

# A plain fetch is quick.
HTTP_TIMEOUT_SECONDS = 45


@dataclass(frozen=True, slots=True)
class Refusal:
    """A "do not try the browser" answer, with the reason for the agent.

    The third outcome the cheap path needed and did not have. It used to
    collapse every failure to `None`, which means "escalate" -- so a site
    that refuses our address was retried through a browser it refuses
    identically, at seven seconds and one of only five render slots.
    """

    message: str
    status: int | None
    blocked_by: str | None


def refusal_for(
    url: str, exc: PageFetchError, *, can_download: bool = True
) -> Refusal | None:
    """What to tell the agent, when the browser would not help."""
    if isinstance(exc, NotAWebPageError):
        return Refusal(
            message=(
                f"{url} serves {exc.content_type}, not a web page, so the "
                "browser was not tried. "
                + (
                    "Download it with `exec_command` (`curl -L -o <file> <url>`) "
                    "if you need the file itself."
                    if can_download
                    else "You have no tool that downloads files; ask the person "
                    "for its contents, or find the same material as a web page."
                )
            ),
            status=exc.status,
            blocked_by=None,
        )
    if isinstance(exc, PageUnreachableFetchError):
        return Refusal(
            message=(
                f"{url} could not be reached ({exc}). The browser was not "
                "tried: it reaches the network the same way and would wait "
                "just as long for the same answer. Check the address, or try "
                "again later."
            ),
            status=None,
            blocked_by=None,
        )
    verdict = exc.verdict
    if verdict is not None and verdict.blocked:
        if verdict.browser_may_help:
            return None
        return Refusal(
            message=(
                f"{url} was refused by a bot defence ({verdict.vendor}). The "
                "sandbox browser reaches the site from the same address and "
                "was measured to be refused identically, so it was not tried. "
                "Use a different source, or ask the person for the page."
            ),
            status=exc.status,
            blocked_by=verdict.vendor,
        )
    if exc.status in (404, 410):
        return Refusal(
            message=(
                f"The page is not there (HTTP {exc.status}). The browser was "
                "not tried -- it would get the same answer."
            ),
            status=exc.status,
            blocked_by=None,
        )
    return None


async def write_markdown(session, *, path: str, content: str) -> None:
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
        path, content.encode("utf-8"), timeout=HTTP_TIMEOUT_SECONDS
    )


async def write_extracted(
    session, *, url: str, page: ExtractedPage, out_dir: str
) -> WebFetchPage | None:
    """Save a cleaned page, or None when the write itself failed."""
    document = document_of(page)
    markdown_path = f"{out_dir}/{slugify(url)}.{_extension(page)}"
    try:
        await write_markdown(session, path=markdown_path, content=document)
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
        files={"text" if page.raw else "markdown": markdown_path},
        preview=document[:PREVIEW_CHARS],
        characters=len(document),
        fetched_with="http",
        status=page.status,
        notice=status_notice(page),
    )


def document_of(page: ExtractedPage) -> str:
    """What is saved, or handed back: an article with its provenance, or a
    text document exactly as it came -- a header would make JSON unparseable."""
    return page.markdown if page.raw else render_document(page)


def inline_page(url: str, page: ExtractedPage, *, limit: int) -> WebFetchPage:
    """A fetched page handed back in the result, for an agent with no tool
    that could read a saved file."""
    document = document_of(page)
    return WebFetchPage(
        url=url,
        success=True,
        title=page.title,
        content=bounded_tool_text(document, limit=limit, what="page"),
        characters=len(document),
        fetched_with="http",
        status=page.status,
        notice=status_notice(page),
    )


_EXTENSIONS = (
    ("json", "json"),
    ("csv", "csv"),
    ("markdown", "md"),
    ("yaml", "yaml"),
    ("javascript", "js"),
)


def _extension(page: ExtractedPage) -> str:
    if not page.raw:
        return "md"
    content_type = (page.content_type or "").lower()
    return next((ext for marker, ext in _EXTENSIONS if marker in content_type), "txt")


def status_notice(page: ExtractedPage) -> str | None:
    """Said only when the status and the page disagree.

    Measured: reuters.com/technology/ answered 429 and served the whole
    article. Throwing that away because of the number would cost a browser
    render for a page we already had.
    """
    if page.status < 400:
        return None
    return (
        f"The site answered HTTP {page.status} but served the article anyway, "
        "so it was saved. Re-request with `render=true` if it looks short."
    )
