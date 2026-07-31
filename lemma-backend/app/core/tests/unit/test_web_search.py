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
