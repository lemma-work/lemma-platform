"""Web research: verticals, filters, and capturing pages into the workspace.

Before this the agent had one tool that returned snippets, plus a shell script
(`save-webpage`) it had to be told about in ten lines of prose — and the same
prose told it to run `lemma tools web-search`, a CLI command that does not
exist. Snippets are not sources; reading a page had no first-class path.
"""

from __future__ import annotations

import asyncio
import shlex
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
from pydantic import ValidationError

from app.modules.agent.tools.web.models import WebFetchRequest

pytestmark = pytest.mark.unit


class TestDomainOperators:
    def test_include_and_exclude_become_site_operators(self) -> None:
        query = apply_domain_operators(
            "transformer architecture",
            include_domains=["arxiv.org"],
            exclude_domains=["pinterest.com"],
        )
        assert query == ("transformer architecture site:arxiv.org -site:pinterest.com")

    def test_a_leading_wildcard_is_tolerated(self) -> None:
        assert apply_domain_operators("x", include_domains=["*.gov.uk"]) == (
            "x site:gov.uk"
        )

    def test_blank_entries_are_ignored(self) -> None:
        assert apply_domain_operators("x", include_domains=["", "  "]) == "x"


class TestVerticalSupport:
    def test_brave_and_searxng_serve_every_vertical(self) -> None:
        every_vertical = {
            SearchVertical.WEB,
            SearchVertical.NEWS,
            SearchVertical.IMAGES,
            SearchVertical.VIDEOS,
        }
        # Named explicitly rather than iterating the enum, so adding a vertical
        # fails here instead of silently widening what "every" means.
        assert set(SearchVertical) == every_vertical
        for client in (BraveSearchClient(), SearXNGSearchClient()):
            assert client.supported_verticals >= every_vertical, client.source

    def test_duckduckgo_is_honest_about_being_web_only(self) -> None:
        """It scrapes HTML; claiming video support would return blog posts."""
        client = DuckDuckGoSearchClient()
        assert client.supports(SearchVertical.WEB)
        assert not client.supports(SearchVertical.VIDEOS)
        assert not client.supports(SearchVertical.NEWS)

    def test_brave_maps_each_vertical_to_its_own_endpoint(self) -> None:
        endpoints = set(BraveSearchClient._ENDPOINTS.values())
        assert len(endpoints) == len(BraveSearchClient._ENDPOINTS), "must be distinct"
        # Anchored: a substring check would also accept
        # https://evil.test/api.search.brave.com/.
        assert all(url.startswith("https://api.search.brave.com/") for url in endpoints)

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
    """A sandbox with a filesystem, because `web_fetch` now reports what is on it.

    Modelling the files rather than just recording commands is what lets these
    tests distinguish "the browser captured the page" from "the browser ran and
    produced nothing", which is the difference the tool used to get wrong.
    """

    def __init__(
        self,
        *,
        responses=None,
        write_error: Exception | None = None,
        browser_writes_nothing: bool = False,
    ):
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self._responses = responses or {}
        self._write_error = write_error
        self._browser_writes_nothing = browser_writes_nothing

    # `writes` names only what the file API put there, so a test can still
    # assert the cheap path did not go through the browser.
    @property
    def writes(self) -> dict[str, bytes]:
        return {path: data for path, data in self.files.items() if path in self._api}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    _api: set

    async def write_file(self, path: str, data: bytes, *, timeout: int = 60):
        if self._write_error is not None:
            raise self._write_error
        self.files[path] = data
        self._api = getattr(self, "_api", set()) | {path}
        return

    def _capture_with_browser(self, cmd: str) -> None:
        """Emulate `save-webpage`: write the files it was asked to emit."""
        if self._browser_writes_nothing:
            return
        parts = shlex.split(cmd)
        out = parts[parts.index("--out") + 1]
        name = parts[parts.index("--name") + 1]
        formats = parts[parts.index("--formats") + 1].split(",")
        suffix = {"markdown": "md", "pdf": "pdf", "jpeg": "jpg", "png": "png"}
        for fmt in formats:
            if fmt in suffix:
                self.files[f"{out}/{name}.{suffix[fmt]}"] = b"# A Title\n\nBody text."

    async def exec_command(self, *, cmd: str, timeout: int = 60, **_kwargs):
        self.commands.append(cmd)
        for marker, response in self._responses.items():
            if marker in cmd:
                return response
        if "save-webpage" in cmd:
            self._capture_with_browser(cmd)
            return {"exit_code": 0, "stdout": ""}
        if cmd.startswith("for f in "):  # the presence-and-size probe
            found = [
                f"{len(data)} {path}"
                for path, data in self.files.items()
                if shlex.quote(path) in cmd and data
            ]
            return {"exit_code": 0, "stdout": "\n".join(found)}
        if cmd.startswith("head -c"):
            path = shlex.split(cmd)[-1]
            return {"exit_code": 0, "stdout": self.files.get(path, b"").decode()}
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

    monkeypatch.setattr(web_fetch_module, "get_workspace_session", fake_get)
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
        # The document goes through the file API, not a shell command: only
        # `mkdir -p` is executed, and the markdown is written as bytes.
        assert all(cmd.startswith("mkdir -p") for cmd in session.commands)
        written = b"".join(session.writes.values()).decode()
        assert "Real article body." in written
        assert "<html" not in written and "<script" not in written

    @pytest.mark.asyncio
    async def test_a_large_article_is_saved_rather_than_re_rendered(
        self, monkeypatch
    ) -> None:
        """The page that most needs capturing must not be the one that fails.

        The document used to be embedded in a `sh -c` argument, and Linux caps a
        single argument at MAX_ARG_STRLEN (128KB) whatever ARG_MAX says. A full
        Wikipedia article extracts to ~185KB of markdown, so it failed with
        E2BIG — and the caller read that as "needs a browser" and spent a Chrome
        render re-fetching a page it had already read correctly.
        """
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        big = "Ashwin took 537 Test wickets. " * 8000  # ~240KB
        _patch_extraction(monkeypatch, markdown=big)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://en.wikipedia.org/wiki/X"]),
        )

        page = result.pages[0]
        assert page.success and page.fetched_with == "http", page.error
        assert not any("save-webpage" in cmd for cmd in session.commands)
        saved = session.writes[page.files["markdown"]]
        assert len(saved) > 128 * 1024
        # What comes back stays bounded no matter how large the page was.
        assert page.preview and len(page.preview) <= 400

    @pytest.mark.asyncio
    async def test_a_failed_write_falls_back_to_the_browser(self, monkeypatch) -> None:
        """A workspace that refuses the write is not a page that cannot be read."""
        session = _FakeSession(write_error=OSError("no space left on device"))
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown="Real article body. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://example.com/a"]),
        )

        assert result.pages[0].fetched_with == "browser"
        assert any("save-webpage" in cmd for cmd in session.commands)

    @pytest.mark.asyncio
    async def test_the_cheap_path_fetches_the_batch_concurrently(
        self, monkeypatch
    ) -> None:
        """Ten sources are ten independent network waits, not a queue.

        Serially this batch is the sum of every site's latency, which is what
        made a research call look hung.
        """
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        from app.modules.agent.tools.web import page_extract

        in_flight = 0
        peak = 0

        async def fake_fetch(url: str):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.05)
                return page_extract.ExtractedPage(
                    url=url,
                    title="A Title",
                    markdown="Body text here. " * 40,
                    content_type="text/html",
                )
            finally:
                in_flight -= 1

        monkeypatch.setattr(web_fetch_module, "fetch_and_clean", fake_fetch)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=[f"https://e{i}.example/a" for i in range(5)]),
        )

        assert all(page.success for page in result.pages)
        assert peak > 1, "the batch was fetched one URL at a time"

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

    @pytest.mark.asyncio
    async def test_one_crashing_extraction_does_not_sink_the_batch(
        self, monkeypatch
    ) -> None:
        """The cheap path runs under `gather`, so an unhandled error there would
        fail every other page in the same call."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        from app.modules.agent.tools.web import page_extract

        async def fake_fetch(url: str):
            if "boom" in url:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad charset")
            return page_extract.ExtractedPage(
                url=url,
                title="A Title",
                markdown="Body text here. " * 40,
                content_type="text/html",
            )

        monkeypatch.setattr(web_fetch_module, "fetch_and_clean", fake_fetch)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://ok.example/a", "https://boom.example/b"]),
        )

        assert result.success
        by_url = {page.url: page for page in result.pages}
        assert by_url["https://ok.example/a"].success
        # The crashing one escalates on its own rather than taking the batch out.
        assert by_url["https://boom.example/b"].fetched_with == "browser"

    @pytest.mark.asyncio
    async def test_a_browser_that_captured_nothing_is_reported_as_a_failure(
        self, monkeypatch
    ) -> None:
        """Found by running the real tool: Britannica refuses our browser too.

        The page came back `success: true, fetched_with: "browser"` naming a
        markdown file that was never written, and the agent went looking for it.
        Success was inferred from the capture command's exit code; what is on
        disk is the only honest answer.
        """
        session = _FakeSession(browser_writes_nothing=True)
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown=None)  # forces the browser

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://www.britannica.com/biography/X"]),
        )

        page = result.pages[0]
        assert page.success is False, page
        assert page.files == {}, "a path must not be reported for a missing file"
        assert page.error and "refuse" in page.error.lower()
        assert result.success is False

    @pytest.mark.asyncio
    async def test_a_render_that_never_finished_is_not_a_success(
        self, monkeypatch
    ) -> None:
        """A command outliving its wait window reports no exit code at all.

        `exit_code not in (0, None)` treated that as success, so a browser still
        working when the tool gave up returned a filename for a file that did
        not exist yet.
        """
        session = _FakeSession(
            browser_writes_nothing=True,
            responses={"save-webpage": {"exit_code": None, "process_id": "p-1"}},
        )
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown=None)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://spa.example/app"]),
        )

        assert result.pages[0].success is False, result.pages[0]

    @pytest.mark.asyncio
    async def test_only_the_formats_that_landed_are_reported(self, monkeypatch) -> None:
        """A screenshot that failed must not be listed beside the markdown."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://example.com/a"], formats=["markdown", "pdf"]
            ),
        )
        page = result.pages[0]
        assert page.success and set(page.files) == {"markdown", "pdf"}

        # Now the same request against a browser that writes nothing.
        empty = _FakeSession(browser_writes_nothing=True)
        _patch_session(monkeypatch, empty)
        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://example.com/a"], formats=["markdown", "pdf"]
            ),
        )
        assert result.pages[0].files == {}


class TestBrowserCaptureHelpers:
    """The pure/near-pure pieces of the browser path, in isolation.

    ``TestWebFetch`` above already drives `_capture_with_browser` and
    `_present_files` thoroughly through `web_fetch_internal` with a fake
    sandbox filesystem. What that integration coverage cannot see is whether
    the command `_browser_script` actually builds is safe against a URL or
    slug containing shell metacharacters, or how `_present_files`'s own
    stdout-parsing behaves against a line the real probe would never emit --
    both are exercised directly here.
    """

    def test_needs_browser_for_a_render_request_regardless_of_formats(self) -> None:
        assert web_fetch_module._needs_browser(["markdown"], render=True) is True

    def test_needs_browser_for_a_pdf_or_image_format(self) -> None:
        assert web_fetch_module._needs_browser(["pdf"], render=False) is True
        assert web_fetch_module._needs_browser(["jpeg"], render=False) is True
        assert web_fetch_module._needs_browser(["png"], render=False) is True

    def test_markdown_alone_does_not_need_a_browser(self) -> None:
        assert web_fetch_module._needs_browser(["markdown"], render=False) is False
        assert web_fetch_module._needs_browser([], render=False) is False

    def test_browser_script_shape(self) -> None:
        cmd = web_fetch_module._browser_script(
            "https://example.com/a", "research", "example-a", ["markdown", "pdf"]
        )
        assert cmd == (
            "mkdir -p research && save-webpage https://example.com/a "
            "--formats markdown,pdf --out research --name example-a"
        )

    def test_browser_script_quotes_a_url_with_shell_metacharacters(self) -> None:
        """A URL is caller-supplied text, not a trusted command fragment.

        `save-webpage`'s own argument, quoted or not, still runs inside the
        sandbox shell -- a query string is a completely ordinary place for a
        `;`, `$(...)`, or `&&` to show up, and an unquoted URL would hand the
        sandbox shell a second command to run.
        """
        hostile_url = "https://example.com/a?x=1;rm -rf / && echo pwned"
        cmd = web_fetch_module._browser_script(
            hostile_url, "research", "safe-name", ["markdown"]
        )
        # shlex.split proves the shell would see it as one argument, not as
        # `;`, `&&`, or a subshell being interpreted.
        parts = shlex.split(cmd)
        assert hostile_url in parts
        assert "rm" not in parts and "pwned" not in parts

    def test_browser_script_quotes_a_hostile_out_dir_and_name(self) -> None:
        cmd = web_fetch_module._browser_script(
            "https://example.com/a",
            "research/$(whoami)",
            "name`id`",
            ["markdown"],
        )
        parts = shlex.split(cmd)
        assert "research/$(whoami)" in parts
        assert "name`id`" in parts
        # Neither injected form was actually interpreted by the split.
        assert "whoami" not in parts and "id" not in parts

    @pytest.mark.asyncio
    async def test_present_files_returns_empty_without_touching_the_session(
        self,
    ) -> None:
        session = _FakeSession()
        result = await web_fetch_module._present_files(session, [])
        assert result == {}
        assert session.commands == []

    @pytest.mark.asyncio
    async def test_present_files_parses_size_and_path_skipping_malformed_lines(
        self,
    ) -> None:
        session = _FakeSession(
            responses={
                "for f in": {
                    "exit_code": 0,
                    "stdout": (
                        "1500 research/a.md\n"
                        "not-a-size research/broken.md\n"
                        "\n"
                        "42000 research/b.pdf\n"
                    ),
                }
            }
        )

        result = await web_fetch_module._present_files(
            session, ["research/a.md", "research/broken.md", "research/b.pdf"]
        )

        assert result == {"research/a.md": 1500, "research/b.pdf": 42000}


class TestTheBatchDoesNotRepeatItself:
    """Sandbox round trips are the expensive part of an otherwise cheap path.

    Measured at ~80ms each against a real container, so five pages doing the
    same setup five times was half of all the chatter in a call.
    """

    @pytest.mark.asyncio
    async def test_the_output_directory_is_created_once_per_batch(
        self, monkeypatch
    ) -> None:
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown="Real article body. " * 40)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=[
                    "https://example.com/a",
                    "https://example.com/b",
                    "https://example.com/c",
                ],
                formats=["markdown"],
            ),
        )

        assert result.success
        assert len(session.writes) == 3
        # One directory, one `mkdir` — not one per page.
        mkdirs = [cmd for cmd in session.commands if cmd.startswith("mkdir -p")]
        assert len(mkdirs) == 1


class TestUrlsWeWillNotRequestAtAll:
    """`web_fetch` takes a URL the model chose and fetches it from the backend,
    which makes it a server-side fetcher aimed by its input. The guard itself is
    covered in `app/core/tests/unit/test_url_guard.py`; what matters here is
    that *both* paths go through it."""

    @staticmethod
    def _refuse(monkeypatch, reason: str = "private_address") -> None:
        from app.core.net.url_guard import UnsafeUrlError

        async def fake_assert(url, *, policy=None):
            raise UnsafeUrlError("nope", reason=reason)

        monkeypatch.setattr(web_fetch_module, "assert_safe_url", fake_assert)

    @pytest.mark.asyncio
    async def test_a_url_resolving_into_the_private_network_is_refused(
        self, monkeypatch
    ) -> None:
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown="Real article body. " * 40)
        self._refuse(monkeypatch)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://internal.example.com/"]),
        )

        assert not result.success
        assert not result.pages[0].success
        assert "private_address" in (result.pages[0].error or "")
        # Nothing was written, and the sandbox was never asked to do anything.
        assert session.writes == {}

    @pytest.mark.asyncio
    async def test_the_browser_path_is_guarded_too(self, monkeypatch) -> None:
        """The regression this exists for: the in-process fetch reaches the
        network from the backend and `save-webpage` reaches it from inside the
        sandbox, so guarding only the cheap path would leave the browser as an
        unguarded way to the same address."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        self._refuse(monkeypatch, reason="link_local_address")

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["http://169.254.169.254/latest/meta-data/"], render=True
            ),
        )

        assert not result.success
        assert "link_local_address" in (result.pages[0].error or "")
        assert not any("save-webpage" in cmd for cmd in session.commands)

    @pytest.mark.asyncio
    async def test_one_refused_url_does_not_sink_the_others(self, monkeypatch) -> None:
        from app.core.net.url_guard import UnsafeUrlError

        async def fake_assert(url, *, policy=None):
            if "internal" in url:
                raise UnsafeUrlError("nope", reason="private_address")
            return url

        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown="Real article body. " * 40)
        monkeypatch.setattr(web_fetch_module, "assert_safe_url", fake_assert)

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(
                urls=["https://internal.example.com/", "https://example.com/a"]
            ),
        )

        assert result.success
        assert not result.pages[0].success
        assert result.pages[1].success


class TestTheToolAlwaysReturns:
    """A tool that blocks takes its whole agent run with it.

    Nothing in the harness imposes a per-tool timeout, and the streaq job
    ceiling that eventually fires records the job as *succeeded* — so a stuck
    `web_fetch` presents to the user as a conversation that simply stopped.
    That is what these pin: the call is bounded, and being cut short still
    returns what it captured.
    """

    @pytest.mark.asyncio
    async def test_the_batch_returns_when_it_runs_out_of_budget(
        self, monkeypatch
    ) -> None:
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        monkeypatch.setattr(web_fetch_module, "_BATCH_BUDGET_SECONDS", 0.3)

        from app.modules.agent.tools.web import page_extract

        async def slow_fetch(url: str):
            if "slow" in url:
                await asyncio.sleep(30)  # never completes inside the budget
            return page_extract.ExtractedPage(
                url=url,
                title="A Title",
                markdown="Body text here. " * 40,
                content_type="text/html",
            )

        monkeypatch.setattr(web_fetch_module, "fetch_and_clean", slow_fetch)

        result = await asyncio.wait_for(
            web_fetch_module.web_fetch_internal(
                SimpleNamespace(),
                WebFetchRequest(
                    urls=["https://slow.example/a", "https://slow.example/b"]
                ),
            ),
            timeout=10,
        )

        # Every requested URL is accounted for, none of them silently missing.
        assert len(result.pages) == 2
        assert all(not page.success for page in result.pages)
        assert all(
            "not attempted" in (page.error or "").lower() for page in result.pages
        )

    @pytest.mark.asyncio
    async def test_pages_captured_before_the_deadline_are_still_returned(
        self, monkeypatch
    ) -> None:
        """The whole point of a partial answer: three of four beats nothing."""
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        # 2s, not the 0.5s this used to run with. The budget has to outlast
        # acquiring the session and capturing the quick page, and on a loaded
        # CI runner under `coverage run` line tracing that setup does not
        # reliably fit in half a second: this test failed with *both* pages
        # reported "Not attempted", i.e. the deadline fired before the first
        # fetch was even reached. That is event-loop starvation, not the
        # behaviour under test, and the margin is the only thing that decided
        # it. Production allows 240s; the point here is that the budget is
        # finite, not what it is.
        monkeypatch.setattr(web_fetch_module, "_BATCH_BUDGET_SECONDS", 2.0)
        monkeypatch.setattr(web_fetch_module, "_MAX_CONCURRENT_FETCHES", 1)

        from app.modules.agent.tools.web import page_extract

        # Set when the quick page has been fetched, so the slow one cannot
        # start spending the budget before it. The semaphore above already
        # serialises them in list order, but that is a scheduling detail --
        # this makes "quick first" the test's own guarantee rather than
        # something inherited from `gather`.
        quick_fetched = asyncio.Event()

        async def fetch(url: str):
            if "slow" in url:
                await quick_fetched.wait()
                # Nothing finishes this but the batch deadline, which is
                # exactly what the assertions below are about. A fixed sleep
                # would be a second race against the budget.
                await asyncio.sleep(3600)
            page = page_extract.ExtractedPage(
                url=url,
                title="A Title",
                markdown="Body text here. " * 40,
                content_type="text/html",
            )
            quick_fetched.set()
            return page

        monkeypatch.setattr(web_fetch_module, "fetch_and_clean", fetch)

        result = await asyncio.wait_for(
            web_fetch_module.web_fetch_internal(
                SimpleNamespace(),
                WebFetchRequest(
                    urls=["https://quick.example/a", "https://slow.example/b"]
                ),
            ),
            timeout=10,
        )

        by_url = {page.url: page for page in result.pages}
        assert by_url["https://quick.example/a"].success, result
        assert not by_url["https://slow.example/b"].success
        assert result.success, "a partial capture is still a useful result"
        assert "time budget" in (result.message or "")

    def test_web_fetch_limits_agree(self) -> None:
        """The tool must not accept more pages than it will render.

        These were 10 and 3. A caller who sent ten JS-heavy URLs got seven back
        as "Skipped: this call renders at most 3" -- a limit the schema had no
        way to express, discovered only after paying for the call. Holding them
        equal is what makes the advertised cap the real one.
        """
        accepted = WebFetchRequest.model_fields["urls"].metadata
        max_urls = next(
            item.max_length for item in accepted if hasattr(item, "max_length")
        )

        assert max_urls == web_fetch_module._MAX_BROWSER_RENDERS

    @pytest.mark.asyncio
    async def test_a_full_batch_of_js_pages_skips_nothing(self, monkeypatch) -> None:
        """The largest batch the schema accepts, every page needing a browser.

        This is the shape that hung in production: a research batch where most
        of the list refuses a plain fetch. It must now render all of them
        rather than reporting some as skipped.
        """
        session = _FakeSession()
        _patch_session(monkeypatch, session)
        _patch_extraction(monkeypatch, markdown=None)  # everything needs a browser
        urls = [
            f"https://spa{index}.example/app"
            for index in range(web_fetch_module._MAX_BROWSER_RENDERS)
        ]

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(), WebFetchRequest(urls=urls)
        )

        rendered = sum("save-webpage" in cmd for cmd in session.commands)
        assert rendered == len(urls), session.commands
        assert [p for p in result.pages if p.error and "Skipped" in p.error] == []

    @pytest.mark.asyncio
    async def test_an_unreachable_workspace_reports_every_url(
        self, monkeypatch
    ) -> None:
        """The failure must name the pages, not just the workspace."""

        async def no_session(ctx, *, session_id, close_on_exit):
            raise RuntimeError("sandbox is not available")

        monkeypatch.setattr(web_fetch_module, "get_workspace_session", no_session)
        monkeypatch.setattr(
            web_fetch_module,
            "workspace_runtime_context",
            lambda ctx: SimpleNamespace(default_shell_session_id="shell-1"),
        )

        result = await web_fetch_module.web_fetch_internal(
            SimpleNamespace(),
            WebFetchRequest(urls=["https://a.example/1", "https://b.example/2"]),
        )

        assert result.success is False
        assert len(result.pages) == 2
        assert result.error and "workspace" in result.error.lower()

    def test_the_url_list_is_capped(self) -> None:
        """At exactly what the tool can render -- see test_web_fetch_limits_agree."""
        limit = web_fetch_module._MAX_BROWSER_RENDERS
        WebFetchRequest(urls=[f"https://e{i}.example/a" for i in range(limit)])
        with pytest.raises(ValidationError):
            WebFetchRequest(urls=[f"https://e{i}.example/a" for i in range(limit + 1)])
