from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.web_search.search_client import (
    AvailableSearchEngines,
    BaseSearchClient,
    DuckDuckGoHTMLParser,
    DuckDuckGoSearchClient,
    SearchClient,
    SearXNGSearchClient,
)
from app.core.web_search.web_search import WebSearchRequest, search_web


def test_duckduckgo_html_parser_extracts_snippet_after_url_block() -> None:
    parser = DuckDuckGoHTMLParser()

    parser.feed(
        """
        <div class="result__body">
          <h2>
            <a class="result__a"
               href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">
              Example <b>Docs</b>
            </a>
          </h2>
          <div class="result__extras">extra metadata</div>
          <a class="result__snippet">
            Official <b>API</b> docs and guides.
          </a>
        </div>
        """
    )

    assert parser.results == [
        {
            "title": "Example Docs",
            "url": "https://example.com/docs",
            "snippet": "Official API docs and guides.",
        }
    ]


def test_redirect_unwrapping_only_trusts_real_duckduckgo_hosts() -> None:
    """`/l/?uddg=` is DuckDuckGo's redirector; unwrapping it trusts the host.

    A lookalike host satisfying the old `endswith` check could put any URL in
    `uddg` and have it returned as the search result.
    """
    normalize = DuckDuckGoHTMLParser._normalize_url
    target = "https%3A%2F%2Fattacker.test%2Fpayload"

    for host in ("evil-duckduckgo.com", "duckduckgo.com.attacker.test"):
        hostile = f"https://{host}/l/?uddg={target}"
        assert normalize(hostile) == hostile

    for host in ("duckduckgo.com", "html.duckduckgo.com"):
        assert (
            normalize(f"https://{host}/l/?uddg={target}")
            == "https://attacker.test/payload"
        )

    # Userinfo must not smuggle the trusted name past the check either.
    smuggled = "https://duckduckgo.com@attacker.test/l/?uddg=" + target
    assert normalize(smuggled) == smuggled


# -- a refusal is not an empty answer -------------------------------------------


def _client(provider: BaseSearchClient) -> SearchClient:
    """A `SearchClient` pinned to one provider, whatever this host configures."""
    client = SearchClient(search_engine=AvailableSearchEngines.DUCKDUCKGO)
    client.search_engine = provider
    client.provider_is_unconfigured = False
    return client


def _http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_duckduckgo_bot_check_is_a_failed_search_not_an_empty_one() -> None:
    """The challenge comes back as a 202 with no result markup, which
    `raise_for_status` passes -- so it was reported as "nothing exists"."""
    page = '<html><body><div class="anomaly-modal">Are you human?</div></body></html>'
    http = _http(lambda request: httpx.Response(202, text=page))

    response = await search_web(
        WebSearchRequest(query="quarterly results"),
        client=_client(DuckDuckGoSearchClient(http=http)),
    )

    assert response.success is False
    assert response.error and "bot check" in response.error


async def test_a_genuinely_empty_search_says_it_may_not_be_the_whole_story() -> None:
    page = "<html><body><div class='no-results'>No results.</div></body></html>"
    http = _http(lambda request: httpx.Response(200, text=page))

    response = await search_web(
        WebSearchRequest(query="zxqv nonsense"),
        client=_client(DuckDuckGoSearchClient(http=http)),
    )

    assert response.success is True
    assert response.results == []
    assert response.note and response.note.startswith("0 results")


async def test_searxng_with_every_engine_down_is_a_failed_search(monkeypatch) -> None:
    monkeypatch.setattr(settings, "searxng_url", "http://searxng.test")
    payload = {"results": [], "unresponsive_engines": [["google", "timeout"]]}
    http = _http(lambda request: httpx.Response(200, json=payload))

    response = await search_web(
        WebSearchRequest(query="x"), client=_client(SearXNGSearchClient(http=http))
    )

    assert response.success is False
    assert response.error and "google (timeout)" in response.error


async def test_searxng_with_some_engines_down_says_results_may_be_partial(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "searxng_url", "http://searxng.test")
    payload = {
        "results": [{"title": "T", "url": "https://example.com", "content": "c"}],
        "unresponsive_engines": [["bing", "CAPTCHA"]],
    }
    http = _http(lambda request: httpx.Response(200, json=payload))

    response = await search_web(
        WebSearchRequest(query="x"), client=_client(SearXNGSearchClient(http=http))
    )

    assert response.success is True
    assert response.note and "bing (CAPTCHA)" in response.note


async def test_a_failed_search_says_why() -> None:
    """It answered "Web search failed" with no reason, logged at debug, so
    neither the agent nor an operator could tell an outage from a typo."""
    http = _http(lambda request: httpx.Response(503, text="overloaded"))

    response = await search_web(
        WebSearchRequest(query="x"),
        client=_client(DuckDuckGoSearchClient(http=http)),
    )

    assert response.success is False
    assert response.error and "HTTP 503" in response.error


@pytest.mark.parametrize("max_results", [0, 21])
def test_max_results_is_bounded(max_results: int) -> None:
    with pytest.raises(ValidationError):
        WebSearchRequest(query="x", max_results=max_results)
