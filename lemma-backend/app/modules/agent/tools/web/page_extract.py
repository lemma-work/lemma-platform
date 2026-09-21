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

from collections.abc import Awaitable, Callable
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
    normalized_headers,
)
from app.core.net.url_guard import UnsafeUrlError, fetch_guarded
from app.modules.agent.config import agent_settings
from app.modules.agent.tools.web.blocked import (
    BLOCK_SNIPPET_BYTES,
    NOT_BLOCKED,
    BlockVerdict,
    classify,
)

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


#: What `fetch_and_clean` calls to get bytes. Named so the test seam is
#: a parameter rather than a patch.
_Fetcher = Callable[[str], Awaitable["FetchedBody"]]


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    title: str | None
    markdown: str
    content_type: str | None
    #: Where the redirects ended. Dropped before; a caller that reports the
    #: URL it asked for cannot say where the answer came from.
    final_url: str = ""
    #: What the site answered. A page can arrive under a status that
    #: disagrees with it -- measured, reuters.com served a whole article
    #: under 429 -- so this travels with the page rather than deciding it.
    status: int = 200
    #: Whether a bot defence refused us. `NOT_BLOCKED` by default, so a
    #: caller constructing one of these by hand gets today's behaviour.
    verdict: BlockVerdict = NOT_BLOCKED


#: Statuses where the body was measured to be the real page rather than an
#: error page, so it is worth reading instead of re-fetching through a
#: browser: `reuters.com/technology/` answered 429 with 6,935 characters of
#: article, and `g2.com` answered 403 with 5,057 characters of real content.
#: Everything else >=400 is treated as an error, as it always was -- a 404's
#: body is boilerplate, not the thing that was asked for.
_MAY_STILL_CARRY_THE_PAGE = frozenset({403, 429})


class PageFetchError(RuntimeError):
    """The page could not be fetched or contained nothing readable."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        verdict: BlockVerdict | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.verdict = verdict


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


async def _fetched_or_raised(url: str) -> FetchedBody:
    """Every status as data; only "could not reach it at all" as an error.

    `fetch_guarded_impersonated` raises on >=400 and carries the headers and
    body with it, so the shape above this can stop caring which side of 400
    a response landed on -- which is the whole point, because the status is
    not what decides whether there is a page here.

    The fallback httpx path raises from inside `client.stream`, so its
    headers are readable and its body is not: the header rules work there
    and the body rules go dark. That is an honest degradation for a path
    that is off by default.
    """
    try:
        return await _fetch_page(url)
    except HttpStatusError as exc:
        return FetchedBody(
            body=exc.body,
            content_type=exc.headers.get("content-type"),
            final_url=url,
            status=exc.status_code,
            headers=exc.headers,
        )
    except httpx.HTTPStatusError as exc:
        return FetchedBody(
            body=b"",
            content_type=exc.response.headers.get("content-type"),
            final_url=url,
            status=exc.response.status_code,
            headers=normalized_headers(exc.response.headers),
        )


async def fetch_and_clean(
    url: str, *, fetch: _Fetcher = _fetched_or_raised
) -> ExtractedPage:
    """Fetch ``url`` and return it as clean markdown.

    Raises `PageFetchError` when the page cannot be retrieved, was refused by
    a bot defence, or holds no readable article. Those are three different
    things and the caller now needs to tell them apart, so the error carries
    the status and the verdict.

    `fetch` is injectable so this can be tested at all. Nothing in the unit
    suite exercised this function before, because the only way in was to
    patch a name inside its own module -- a double in the subject, which the
    architecture gate counts and which certifies the half nobody wrote.
    """
    try:
        page = await fetch(url)
    except UnsafeUrlError as exc:
        # Not escalated to the browser: the sandbox would reach the same address
        # from inside the network, which is worse.
        raise PageFetchError(
            f"That URL is not a permitted fetch target ({exc.reason})."
        ) from exc
    except (PageUnreachableError, httpx.HTTPError) as exc:
        raise PageFetchError(
            f"The page could not be reached ({type(exc).__name__})."
        ) from exc

    snippet = page.body[:BLOCK_SNIPPET_BYTES].decode("utf-8", "replace")
    verdict = classify(status=page.status, headers=page.headers, body_snippet=snippet)
    # Classified before the article is extracted, so a challenge page never
    # costs a CPU-limiter slot for markup nobody wants.
    if verdict.blocked:
        raise PageFetchError(
            f"The site refused the request (HTTP {page.status}).",
            status=page.status,
            verdict=verdict,
        )

    if page.status >= 400 and page.status not in _MAY_STILL_CARRY_THE_PAGE:
        # Everything else keeps the old behaviour: a >=400 is an error, and
        # its body is an error page, not the article. Accepting any body
        # under any status made `example.com/shell` -- a real 404 whose body
        # is the ordinary Example Domain boilerplate -- come back as a
        # successful capture of an article that does not exist.
        raise PageFetchError(
            f"The site returned HTTP {page.status}.",
            status=page.status,
            verdict=verdict,
        )

    content_type = page.content_type
    if not _looks_like_html(content_type):
        raise PageFetchError(
            f"That URL is {content_type or 'not a web page'}, not an article. "
            "Download it with `exec_command` if you need the file itself.",
            status=page.status,
        )

    title, markdown = await extract_markdown_off_loop(page.body, url=url)
    if not markdown:
        raise PageFetchError(
            "No readable article was found — the page probably renders its "
            "content with JavaScript.",
            status=page.status,
            verdict=verdict,
        )
    return ExtractedPage(
        url=url,
        title=title,
        markdown=markdown,
        content_type=content_type,
        final_url=page.final_url or url,
        status=page.status,
        verdict=verdict,
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
