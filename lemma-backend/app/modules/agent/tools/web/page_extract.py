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
from app.core.net.impersonating_client import (
    FetchedBody,
    HttpStatusError,
    PageUnreachableError,
    fetch_guarded_impersonated,
    web_page_policy,
)
from app.core.net.url_guard import UnsafeUrlError, fetch_guarded
from app.modules.agent.config import agent_settings

logger = get_logger(__name__)

# Enough for a long article; past this a page is a download, not something to
# read, and the browser path handles it better anyway.
MAX_HTML_BYTES = 8 * 1024 * 1024

_FETCH_TIMEOUT_SECONDS = 25

# Only for the fallback client. On the impersonating path the headers come from
# the client itself and have to stay coherent with the TLS fingerprint — see
# `_fetch_page`. This header never got us past a site that fingerprints anyway:
# that decision is made during the handshake, before any header is read.
_USER_AGENT = (
    "Mozilla/5.0 (compatible; LemmaAgent/1.0; +https://lemma.sh/bot) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_REQUEST_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


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


async def extract_markdown_off_loop(
    html: str | bytes, *, url: str
) -> tuple[str | None, str]:
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


def extract_markdown(html: str | bytes, *, url: str) -> tuple[str | None, str]:
    """Return ``(title, markdown)`` for a page's HTML.

    Accepts raw bytes as well as text: trafilatura does its own encoding
    detection, which is better evidence than a charset guessed from a header,
    and it saves decoding every page twice. Verified to produce byte-identical
    output either way.

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


async def _fetch_page(url: str) -> FetchedBody:
    """Retrieve the raw page through whichever client is configured.

    Both paths go through the SSRF guard, which re-validates every redirect hop
    and cuts the body off at ``MAX_HTML_BYTES`` mid-transfer — the size used to
    be measured only once the whole thing was already in memory.
    """
    if agent_settings.web_fetch_impersonate_browser:
        # Deliberately no headers of our own. The client sends a complete,
        # self-consistent Chrome header set, and overriding one of them puts a
        # scripted-client `User-Agent` behind a Chrome TLS handshake -- a
        # mismatch that sites read as exactly the thing they are blocking.
        # Measured: sending ours cost Reuters (401 with it, 200 without).
        return await fetch_guarded_impersonated(
            url,
            max_bytes=MAX_HTML_BYTES,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    # The kill switch: the plain client, still guarded and still under the same
    # policy, for backing the new dependency out without a redeploy.
    body = await fetch_guarded(
        get_shared_http_client(),
        url,
        max_bytes=MAX_HTML_BYTES,
        timeout=_FETCH_TIMEOUT_SECONDS,
        headers=_REQUEST_HEADERS,
        policy=web_page_policy(),
    )
    return FetchedBody(body=body, content_type=None, final_url=url)


async def fetch_and_clean(url: str) -> ExtractedPage:
    """Fetch ``url`` and return it as clean markdown.

    Raises `PageFetchError` when the page cannot be retrieved or holds no
    readable article — the caller escalates those to the browser rather than
    reporting an empty page as a success.
    """
    try:
        page = await _fetch_page(url)
    except UnsafeUrlError as exc:
        # Not escalated to the browser: the sandbox would reach the same address
        # from inside the network, which is worse.
        raise PageFetchError(
            f"That URL is not a permitted fetch target ({exc.reason})."
        ) from exc
    except HttpStatusError as exc:
        raise PageFetchError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise PageFetchError(
            f"The site returned HTTP {exc.response.status_code}."
        ) from exc
    except (PageUnreachableError, httpx.HTTPError) as exc:
        raise PageFetchError(
            f"The page could not be reached ({type(exc).__name__})."
        ) from exc

    content_type = page.content_type
    if not _looks_like_html(content_type):
        raise PageFetchError(
            f"That URL is {content_type or 'not a web page'}, not an article. "
            "Download it with `exec_command` if you need the file itself."
        )

    title, markdown = await extract_markdown_off_loop(page.body, url=url)
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
