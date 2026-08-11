"""Fetch a page and reduce it to clean markdown, in-process.

Raw HTML must never reach the agent. A modern article page is mostly navigation,
cookie banners, related-post rails and script tags; handing that to a model
costs thousands of tokens to say what a few hundred would. So extraction happens
here, before anything is written to the workspace or previewed back.

`trafilatura` does the boilerplate removal (Apache-2.0, emits markdown directly).
Doing it in the backend rather than in the sandbox also removes a container
round-trip from the common case: a static page is fetched and cleaned without
the workspace being involved at all.

The browser in the workspace stays the fallback, for pages that only exist after
JavaScript runs and for anything that needs a PDF or a screenshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import httpx

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.core.net.http_client import get_shared_http_client

logger = get_logger(__name__)

# Enough for a long article; past this a page is a download, not something to
# read, and the browser path handles it better anyway.
MAX_HTML_BYTES = 8 * 1024 * 1024

_FETCH_TIMEOUT_SECONDS = 25

# Presented to sites as a normal-ish client. Some refuse an obviously scripted
# agent outright, and a 403 sends us to the browser for no good reason.
_USER_AGENT = (
    "Mozilla/5.0 (compatible; LemmaAgent/1.0; +https://lemma.sh/bot) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    title: str | None
    markdown: str
    content_type: str | None


class PageFetchError(RuntimeError):
    """The page could not be fetched or contained nothing readable."""


def _looks_like_html(content_type: str | None) -> bool:
    if not content_type:
        return True  # no header: let the extractor decide
    return "html" in content_type.lower() or "xml" in content_type.lower()


async def extract_markdown_off_loop(html: str, *, url: str) -> tuple[str | None, str]:
    """`extract_markdown` on a worker thread.

    Boilerplate removal is pure CPU and it scales with page size: a full
    Wikipedia article (1.2MB of HTML) measures at ~1.1 seconds. On the event
    loop that is 1.1 seconds in which the worker publishes no SSE frames, runs
    no other agent's tools and answers no stop-checks — and a research batch
    does it once per URL. `run_blocking` is the house rule for exactly this
    (see `app/core/concurrency/offload`).
    """
    return await run_blocking(
        partial(extract_markdown, html, url=url), limiter="cpu_bound"
    )


def extract_markdown(html: str, *, url: str) -> tuple[str | None, str]:
    """Return ``(title, markdown)`` for a page's HTML.

    Kept separate from fetching so it can be tested without the network, and so
    the browser path can reuse it on HTML it captured itself. Call
    `extract_markdown_off_loop` from async code.
    """
    import trafilatura

    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        # Comment sections are almost never what the agent asked for and are
        # frequently longer than the article.
        include_comments=False,
        favor_precision=True,
    )
    title: str | None = None
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata is not None:
            title = metadata.title or None
    except Exception:  # pragma: no cover - metadata is best-effort
        title = None
    return title, (markdown or "").strip()


async def fetch_and_clean(url: str) -> ExtractedPage:
    """Fetch ``url`` and return it as clean markdown.

    Raises `PageFetchError` when the page cannot be retrieved or holds no
    readable article — the caller escalates those to the browser rather than
    reporting an empty page as a success.
    """
    client = get_shared_http_client()
    try:
        response = await client.get(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise PageFetchError(
            f"The site returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.HTTPError as exc:
        raise PageFetchError(
            f"The page could not be reached ({type(exc).__name__})."
        ) from exc

    content_type = response.headers.get("content-type")
    if not _looks_like_html(content_type):
        raise PageFetchError(
            f"That URL is {content_type or 'not a web page'}, not an article. "
            "Download it with `exec_command` if you need the file itself."
        )
    if len(response.content) > MAX_HTML_BYTES:
        raise PageFetchError("The page is too large to extract in one call.")

    title, markdown = await extract_markdown_off_loop(response.text, url=url)
    if not markdown:
        raise PageFetchError(
            "No readable article was found — the page probably renders its "
            "content with JavaScript."
        )
    return ExtractedPage(
        url=url, title=title, markdown=markdown, content_type=content_type
    )


def render_document(page: ExtractedPage) -> str:
    """The markdown file written to the workspace, with its provenance."""
    header = [
        f"# {page.title}" if page.title else "# Untitled",
        "",
        f"Source: {page.url}",
        "",
    ]
    return "\n".join(header) + page.markdown + "\n"
