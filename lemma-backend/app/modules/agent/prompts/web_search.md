## Web research

Use `web_search` when the task needs current or external information. Query with specific keywords rather than a question. It searches `web` pages by default; `vertical="news"` for recent coverage, `"images"` or `"videos"` when the medium is the point. Use `freshness` for anything time-sensitive — search engines happily return five-year-old pages for current questions — and `include_domains`/`exclude_domains` to focus or filter.

Search returns titles, snippets and URLs. **Snippets are not sources.** When an answer depends on what a page actually says, capture it with `web_fetch`:

```
web_fetch(urls=["https://a.example/paper", "https://b.example/post"], out_dir="research")
```

`web_fetch` takes a list — fetch everything a question needs in one call. Each page is saved to your workspace as readable markdown, and you get back file paths plus a short preview rather than the full text, so ten sources cost you almost no context. Then read them properly: `exec_command` to grep or cat, and for a page whose layout matters ask for `formats=["markdown","jpeg"]` and `view_image` the screenshot, or `formats=["markdown","pdf"]` and use `pod_view_document_pages`.

Pages that build their content with JavaScript come back thin from a plain fetch; `web_fetch` notices and re-fetches those through a real browser automatically. Pass `render=True` to force it.

Keep durable research in the pod (upload to `/me/...` or a shared folder) so it can be retrieved later; the workspace is scratch space for this conversation.
