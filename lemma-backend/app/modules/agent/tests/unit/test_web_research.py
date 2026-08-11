"""Web research: verticals, filters, and capturing pages into the workspace.

Before this the agent had one tool that returned snippets, plus a shell script
(`save-webpage`) it had to be told about in ten lines of prose — and the same
prose told it to run `lemma tools web-search`, a CLI command that does not
exist. Snippets are not sources; reading a page had no first-class path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.web_search.search_client import (
    BraveSearchClient,
    DuckDuckGoSearchClient,
    SearchClient,
    SearchFreshness,
    SearchVertical,
    SearXNGSearchClient,
    apply_domain_operators,
)
from app.modules.agent.tools.web import web_fetch as web_fetch_module
from app.modules.agent.tools.web.models import WebFetchRequest

pytestmark = pytest.mark.unit


class TestDomainOperators:
    def test_include_and_exclude_become_site_operators(self) -> None:
        query = apply_domain_operators(
            "transformer architecture",
            include_domains=["arxiv.org"],
            exclude_domains=["pinterest.com"],
        )
        assert query == (
            "transformer architecture site:arxiv.org -site:pinterest.com"
        )

    def test_a_leading_wildcard_is_tolerated(self) -> None:
        assert apply_domain_operators("x", include_domains=["*.gov.uk"]) == (
            "x site:gov.uk"
        )

    def test_blank_entries_are_ignored(self) -> None:
        assert apply_domain_operators("x", include_domains=["", "  "]) == "x"


class TestVerticalSupport:
    def test_brave_and_searxng_serve_every_vertical(self) -> None:
        for client in (BraveSearchClient(), SearXNGSearchClient()):
            for vertical in SearchVertical:
                assert client.supports(vertical), (client.source, vertical)

    def test_duckduckgo_is_honest_about_being_web_only(self) -> None:
        """It scrapes HTML; claiming video support would return blog posts."""
        client = DuckDuckGoSearchClient()
        assert client.supports(SearchVertical.WEB)
        assert not client.supports(SearchVertical.VIDEOS)
        assert not client.supports(SearchVertical.NEWS)

    def test_brave_maps_each_vertical_to_its_own_endpoint(self) -> None:
        endpoints = {
            BraveSearchClient._ENDPOINTS[vertical] for vertical in SearchVertical
        }
        assert len(endpoints) == len(SearchVertical), "endpoints must be distinct"
        assert all("api.search.brave.com" in url for url in endpoints)

    def test_brave_translates_freshness_to_its_own_vocabulary(self) -> None:
        assert BraveSearchClient._FRESHNESS[SearchFreshness.DAY] == "pd"
        assert BraveSearchClient._FRESHNESS[SearchFreshness.YEAR] == "py"


class TestUnsupportedVerticalDegradesHonestly:
    @pytest.mark.asyncio
    async def test_the_caller_is_told_it_got_web_results(self, monkeypatch) -> None:
        """Silently answering a "videos" query with blog posts is worse than
        saying the vertical was unavailable."""
        client = SearchClient(search_engine=None)
        client.search_engine = DuckDuckGoSearchClient()
        client._pinned = "duckduckgo"  # pin so no other provider is considered

        captured: dict[str, object] = {}

        async def fake_search(query, max_results=5, *, vertical, freshness):
            captured["vertical"] = vertical
            return []

        client.search_engine.search = fake_search

        results, note = await client.search(
            "cat videos", vertical=SearchVertical.VIDEOS
        )

        assert results == []
        assert note is not None and "cannot search videos" in note
        # And it actually ran a web search rather than a bogus video one.
        assert captured["vertical"] is SearchVertical.WEB

    @pytest.mark.asyncio
    async def test_a_supported_vertical_carries_no_note(self, monkeypatch) -> None:
        client = SearchClient(search_engine=None)
        client.search_engine = SearXNGSearchClient()
        client._pinned = "searxng"

        async def fake_search(query, max_results=5, *, vertical, freshness):
            return []

        client.search_engine.search = fake_search

        _results, note = await client.search("x", vertical=SearchVertical.NEWS)
        assert note is None


class _FakeSession:
    """Records the shell commands `web_fetch` would run in the sandbox."""

    def __init__(self, *, responses=None):
        self.commands: list[str] = []
        self._responses = responses or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec_command(self, *, cmd: str, timeout: int = 60, **_kwargs):
        self.commands.append(cmd)
        for marker, response in self._responses.items():
            if marker in cmd:
                return response
        if cmd.startswith("head -c"):
            return {"exit_code": 0, "stdout": "# A Title\n\nBody text here."}
        if "wc -c" in cmd:
            return {"exit_code": 0, "stdout": "5000"}
        return {"exit_code": 0, "stdout": ""}


def _patch_extraction(monkeypatch, *, markdown: str | None, title: str = "A Title"):
    """Stand in for the network fetch + trafilatura extraction."""
    from app.modules.agent.tools.web import page_extract

    async def fake_fetch(url: str):
        if markdown is None:
            raise page_extract.PageFetchError(
                "No readable article was found — the page probably renders its "
                "content with JavaScript."
            )
        return page_extract.ExtractedPage(
            url=url, title=title, markdown=markdown, content_type="text/html"
        )

    monkeypatch.setattr(web_fetch_module, "fetch_and_clean", fake_fetch)


def _patch_session(monkeypatch, session) -> None:
    async def fake_get(ctx, *, session_id, close_on_exit):
        return session

    monkeypatch.setattr(web_fetch_module, "_get_workspace_session", fake_get)
    monkeypatch.setattr(
        web_fetch_module,
        "workspace_runtime_context",
        lambda ctx: SimpleNamespace(default_shell_session_id="shell-1"),
    )


class TestWebFetch:
    @pytest.mark.asyncio
    async def test_markdown_only_uses_the_cheap_path(self, monkeypatch) -> None:
        """A static page is fetched and cleaned in-process.

        No browser start-up, and no container round-trip to *fetch* — the
        sandbox is touched only to write the finished markdown. Raw HTML never
        leaves this process.
        """
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown="Real article body. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://example.com/a"], formats=["markdown"]),
        )

        assert result.success
        assert result.pages[0].fetched_with == "http"
        assert not any("save-webpage" in cmd for cmd in session.commands)
        # The only shell command is the write, and it carries cleaned markdown.
        written = " ".join(session.commands)
        assert "Real article body." in written
        assert "<html" not in written and "<script" not in written

    @pytest.mark.asyncio
    async def test_a_screenshot_request_uses_the_browser(self, monkeypatch) -> None:
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://example.com/a"], formats=["markdown", "jpeg"]
            ),
        )

        assert result.pages[0].fetched_with == "browser"
        assert any("save-webpage" in cmd for cmd in session.commands)
        assert "jpeg" in result.pages[0].files

    @pytest.mark.asyncio
    async def test_a_javascript_page_falls_back_to_the_browser(
        self, monkeypatch
    ) -> None:
        """A plain fetch that returns a near-empty shell is not a success."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown=None)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://spa.example/app"], formats=["markdown"]),
        )

        assert result.pages[0].fetched_with == "browser"
        assert any("save-webpage" in cmd for cmd in session.commands)

    @pytest.mark.asyncio
    async def test_one_bad_url_does_not_sink_the_batch(self, monkeypatch) -> None:
        """Ten sources, one dead link: the nine that worked still come back."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        _patch_extraction(monkeypatch, markdown="Body text here. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://good.example/a", "ftp://bad.example/b"],
                formats=["markdown"],
            ),
        )

        assert result.success
        assert result.pages[0].success
        assert not result.pages[1].success
        assert "http(s)" in result.pages[1].error

    @pytest.mark.asyncio
    async def test_page_text_is_previewed_not_returned(self, monkeypatch) -> None:
        """Twenty articles inline would bury the task this tool serves."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        _patch_extraction(monkeypatch, markdown="Body text here. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://example.com/a"]),
        )

        page = result.pages[0]
        assert page.files["markdown"].endswith(".md")
        assert page.preview and len(page.preview) <= 400
        assert page.title == "A Title"
        # The response carries a path and a preview, not the article: whatever
        # the page's length, what comes back stays bounded.
        assert page.characters and page.characters > 600
        assert len(result.model_dump_json()) < page.characters + 600

    @pytest.mark.asyncio
    async def test_urls_are_slugified_into_distinct_files(self, monkeypatch) -> None:
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        _patch_extraction(monkeypatch, markdown="Body text here. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://a.example/one", "https://b.example/two"],
                out_dir="notes",
            ),
        )

        paths = {page.files["markdown"] for page in result.pages}
        assert len(paths) == 2
        assert all(path.startswith("notes/") for path in paths)

    def test_the_batch_is_capped(self) -> None:
        """One call must not become a hundred browser renders."""
        with pytest.raises(Exception):
            WebFetchRequest(urls=[f"https://e.example/{i}" for i in range(50)])
