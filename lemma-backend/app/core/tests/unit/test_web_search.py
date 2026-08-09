from __future__ import annotations

from app.core.web_search.search_client import DuckDuckGoHTMLParser


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
