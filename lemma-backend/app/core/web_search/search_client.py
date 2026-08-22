from enum import Enum
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel

from app.core.config import settings
from app.core.net.domains import host_is_within, hostname_of
from app.core.net.http_client import get_shared_http_client


class SearchVertical(str, Enum):
    """What kind of result the caller wants.

    Not every provider serves every vertical, so `BaseSearchClient` advertises
    what it supports and the caller degrades honestly rather than silently
    returning web pages for a video query.
    """

    WEB = "web"
    NEWS = "news"
    IMAGES = "images"
    VIDEOS = "videos"


class SearchFreshness(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    score: Optional[float] = None
    # Per-vertical extras, left unset where the vertical or provider has no such
    # concept — one result shape serves all four rather than four near-identical
    # models the caller has to branch on.
    published_at: Optional[str] = None
    publisher: Optional[str] = None
    thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None
    duration: Optional[str] = None


class AvailableSearchEngines(str, Enum):
    DUCKDUCKGO = "duckduckgo"
    SEARXNG = "searxng"
    BRAVE = "brave"


def apply_domain_operators(
    query: str,
    *,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> str:
    """Express domain filters as `site:` operators.

    The fallback for providers with no native filter parameter. Every engine
    here understands the operators, so this keeps the tool's contract uniform
    even where the API is not.
    """
    parts = [query]
    for domain in include_domains or []:
        cleaned = domain.strip().lstrip("*.")
        if cleaned:
            parts.append(f"site:{cleaned}")
    for domain in exclude_domains or []:
        cleaned = domain.strip().lstrip("*.")
        if cleaned:
            parts.append(f"-site:{cleaned}")
    return " ".join(parts)


class BaseSearchClient:
    source: str
    # Web only unless a subclass says otherwise: assuming support a provider
    # lacks is how a "videos" query quietly becomes a page of blog posts.
    supported_verticals: frozenset[SearchVertical] = frozenset({SearchVertical.WEB})

    def is_available(self) -> bool:
        return True

    def supports(self, vertical: SearchVertical) -> bool:
        return vertical in self.supported_verticals

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        vertical: SearchVertical = SearchVertical.WEB,
        freshness: Optional[SearchFreshness] = None,
    ) -> List[SearchResult]:
        raise NotImplementedError


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        # Track div nesting so a result is finalized only when its own
        # ``result__body`` block closes, not at the first inner ``</div>``.
        self._depth = 0
        self._body_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        class_names = set(attr.get("class", "").split())
        if tag == "div":
            self._depth += 1
            if self._current is None and "result__body" in class_names:
                self._current = {"title": "", "url": "", "snippet": ""}
                self._body_depth = self._depth
                self._capture = None
            return
        if self._current is None:
            return
        if tag == "a" and "result__a" in class_names:
            self._current["url"] = self._normalize_url(attr.get("href", ""))
            self._capture = "title"
        elif "result__snippet" in class_names:
            self._capture = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            existing = self._current.get(self._capture, "")
            # Collapse whitespace so highlight (<b>) boundaries don't leave
            # double spaces in titles/snippets.
            self._current[self._capture] = " ".join(f"{existing} {data}".split())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture in ("title", "snippet"):
            self._capture = None
        elif tag == "div":
            at_body_close = (
                self._current is not None
                and self._body_depth is not None
                and self._depth == self._body_depth
            )
            self._depth -= 1
            if at_body_close:
                if (
                    self._current
                    and self._current.get("title")
                    and self._current.get("url")
                ):
                    self.results.append(self._current)
                self._current = None
                self._body_depth = None
                self._capture = None

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Unwrap DuckDuckGo's `/l/` redirector to the result's real URL.

        Only for URLs actually on DuckDuckGo. The previous check was
        ``netloc.endswith("duckduckgo.com")``, which a page served from
        ``evil-duckduckgo.com`` satisfies -- and then whatever it put in `uddg`
        became the URL we handed back as the search result.
        """
        url = unescape(url)
        parsed = urlparse(url)
        if host_is_within(hostname_of(url), "duckduckgo.com") and parsed.path == "/l/":
            uddg = parse_qs(parsed.query).get("uddg")
            if uddg:
                return unquote(uddg[0])
        return url


class DuckDuckGoSearchClient(BaseSearchClient):
    source = "duckduckgo"
    # Web only: this scrapes the HTML endpoint, and the vertical pages are not
    # stable enough to parse. Better to say so than to return blog posts for a
    # video query.

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        vertical: SearchVertical = SearchVertical.WEB,
        freshness: Optional[SearchFreshness] = None,
    ) -> List[SearchResult]:
        del vertical, freshness  # unsupported; the caller was told already
        response = await get_shared_http_client().post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "lemma-local-web-search/1.0"},
            timeout=15,
        )
        response.raise_for_status()

        parser = DuckDuckGoHTMLParser()
        parser.feed(response.text)
        return [
            SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item.get("snippet", ""),
                source=self.source,
            )
            for item in parser.results[:max_results]
        ]


class SearXNGSearchClient(BaseSearchClient):
    source = "searxng"
    # SearXNG serves verticals as categories on the same endpoint.
    supported_verticals = frozenset(
        {
            SearchVertical.WEB,
            SearchVertical.NEWS,
            SearchVertical.IMAGES,
            SearchVertical.VIDEOS,
        }
    )

    _CATEGORIES = {
        SearchVertical.WEB: "general",
        SearchVertical.NEWS: "news",
        SearchVertical.IMAGES: "images",
        SearchVertical.VIDEOS: "videos",
    }
    _TIME_RANGE = {
        SearchFreshness.DAY: "day",
        SearchFreshness.WEEK: "week",
        SearchFreshness.MONTH: "month",
        SearchFreshness.YEAR: "year",
    }

    def __init__(self) -> None:
        self.base_url = (settings.searxng_url or "").strip().rstrip("/")

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        vertical: SearchVertical = SearchVertical.WEB,
        freshness: Optional[SearchFreshness] = None,
    ) -> List[SearchResult]:
        if not self.base_url:
            raise ValueError("SEARXNG_URL is not set")
        params: dict[str, object] = {
            "q": query,
            "format": "json",
            "pageno": 1,
            "categories": self._CATEGORIES[vertical],
        }
        if freshness is not None:
            params["time_range"] = self._TIME_RANGE[freshness]
        response = await get_shared_http_client().get(
            f"{self.base_url}/search",
            params=params,
            headers={"Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        raw_results = response.json().get("results", [])
        sorted_results = sorted(
            raw_results,
            key=lambda item: float(item.get("score", 0) or 0),
            reverse=True,
        )
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                source=self.source,
                score=float(item["score"]) if item.get("score") is not None else None,
                published_at=item.get("publishedDate"),
                thumbnail_url=item.get("thumbnail") or item.get("thumbnail_src"),
                image_url=item.get("img_src"),
                duration=item.get("length"),
            )
            for item in sorted_results[:max_results]
        ]


class BraveSearchClient(BaseSearchClient):
    source = "brave"
    # Brave serves each vertical from its own endpoint beside /web/search.
    supported_verticals = frozenset(
        {
            SearchVertical.WEB,
            SearchVertical.NEWS,
            SearchVertical.IMAGES,
            SearchVertical.VIDEOS,
        }
    )

    _ENDPOINTS = {
        SearchVertical.WEB: "https://api.search.brave.com/res/v1/web/search",
        SearchVertical.NEWS: "https://api.search.brave.com/res/v1/news/search",
        SearchVertical.IMAGES: "https://api.search.brave.com/res/v1/images/search",
        SearchVertical.VIDEOS: "https://api.search.brave.com/res/v1/videos/search",
    }
    # Brave's own freshness vocabulary: past day/week/month/year.
    _FRESHNESS = {
        SearchFreshness.DAY: "pd",
        SearchFreshness.WEEK: "pw",
        SearchFreshness.MONTH: "pm",
        SearchFreshness.YEAR: "py",
    }

    def __init__(self) -> None:
        self.api_key = (settings.brave_search_api_key or "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        vertical: SearchVertical = SearchVertical.WEB,
        freshness: Optional[SearchFreshness] = None,
    ) -> List[SearchResult]:
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is not set")
        params: dict[str, object] = {
            "q": query,
            "count": max(1, min(max_results, 20)),
        }
        if freshness is not None:
            params["freshness"] = self._FRESHNESS[freshness]
        response = await get_shared_http_client().get(
            self._ENDPOINTS[vertical],
            params=params,
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        # Only the web endpoint nests its results under a "web" key.
        raw_results = (
            (payload.get("web") or {}).get("results", [])
            if vertical is SearchVertical.WEB
            else payload.get("results", [])
        )
        return [self._to_result(item, vertical) for item in raw_results[:max_results]]

    def _to_result(self, item: dict, vertical: SearchVertical) -> SearchResult:
        thumbnail = item.get("thumbnail")
        thumbnail_url = (
            thumbnail.get("src") if isinstance(thumbnail, dict) else thumbnail
        )
        properties = item.get("properties")
        image_url = properties.get("url") if isinstance(properties, dict) else None
        meta = item.get("meta_url")
        publisher = meta.get("netloc") if isinstance(meta, dict) else None
        return SearchResult(
            title=str(item.get("title", "")),
            url=str(item.get("url", "")),
            snippet=str(item.get("description") or ""),
            source=self.source,
            published_at=item.get("page_age") or item.get("age"),
            publisher=publisher,
            thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
            image_url=str(image_url) if image_url else None,
            duration=item.get("duration")
            if vertical is SearchVertical.VIDEOS
            else None,
        )


_CLIENTS: dict[AvailableSearchEngines, type[BaseSearchClient]] = {
    AvailableSearchEngines.SEARXNG: SearXNGSearchClient,
    AvailableSearchEngines.BRAVE: BraveSearchClient,
    AvailableSearchEngines.DUCKDUCKGO: DuckDuckGoSearchClient,
}

_AUTO_ORDER = (
    AvailableSearchEngines.SEARXNG,
    AvailableSearchEngines.BRAVE,
    AvailableSearchEngines.DUCKDUCKGO,
)


class SearchClient:
    def __init__(self, search_engine: AvailableSearchEngines | None = None):
        self._pinned = search_engine
        self.search_engine = self._get_client(search_engine)
        # True when nothing is configured and the choice fell through to the
        # keyless scraper. A caller deserves to know the difference between a
        # provider that answered "nothing" and no provider being looked at at
        # all -- see PS-OPS-030 and DEV-OPS-005.
        self.is_unconfigured_fallback = (
            search_engine is None
            and settings.web_search_provider.strip().lower() == "auto"
            and isinstance(self.search_engine, DuckDuckGoSearchClient)
        )

    def _get_client(self, engine: AvailableSearchEngines | None) -> BaseSearchClient:
        if engine is not None:
            return _CLIENTS[engine]()
        configured_provider = settings.web_search_provider.strip().lower()
        if configured_provider != "auto":
            return _CLIENTS[AvailableSearchEngines(configured_provider)]()
        for candidate in _AUTO_ORDER:
            client = _CLIENTS[candidate]()
            if client.is_available():
                return client
        return DuckDuckGoSearchClient()

    def _client_for(self, vertical: SearchVertical) -> tuple[BaseSearchClient, bool]:
        """A client that can serve ``vertical``, and whether it is the real thing.

        The default client is one provider chosen at construction; if it cannot
        serve the requested vertical, prefer another configured provider that
        can before falling back to a web search with operators. Returns
        ``(client, is_native)`` so the caller can tell the agent what it got —
        silently answering a "videos" query with blog posts is worse than
        saying the vertical was unavailable.
        """
        if self.search_engine.supports(vertical):
            return self.search_engine, True
        if self._pinned is None:
            for candidate in _AUTO_ORDER:
                client = _CLIENTS[candidate]()
                if client.is_available() and client.supports(vertical):
                    return client, True
        return self.search_engine, False

    async def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        vertical: SearchVertical = SearchVertical.WEB,
        freshness: Optional[SearchFreshness] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> tuple[List[SearchResult], Optional[str]]:
        """Run a search. Returns ``(results, note)``.

        ``note`` is set when the request could not be served as asked — an
        unsupported vertical, say — so the caller can tell the agent instead of
        letting it believe it saw videos.
        """
        if not query:
            raise ValueError("Search query cannot be empty")

        client, native = self._client_for(vertical)
        note: Optional[str] = None
        effective_vertical = vertical
        if not native:
            note = (
                f"The configured search provider ('{client.source}') cannot "
                f"search {vertical.value}; these are ordinary web results."
            )
            effective_vertical = SearchVertical.WEB

        # Domain filters ride as operators: every engine understands them, and
        # not every engine has a native parameter.
        effective_query = apply_domain_operators(
            query, include_domains=include_domains, exclude_domains=exclude_domains
        )
        results = await client.search(
            effective_query,
            max_results,
            vertical=effective_vertical,
            freshness=freshness,
        )
        return results, note
