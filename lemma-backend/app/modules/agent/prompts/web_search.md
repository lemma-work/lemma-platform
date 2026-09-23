## Web research

Use `web_search` for current or external information. Use specific keywords,
`freshness` for time-sensitive queries, and domain filters where useful.
`vertical` defaults to `web`; alternatives are `news`, `images`, and `videos`.

Fetch source pages before relying on snippets. Batch URLs in one call:

```
web_fetch(urls=["https://a.example/paper", "https://b.example/post"], out_dir="research")
```

Read the returned workspace files. For layout, request
`formats=["markdown", "jpeg"]` and inspect with `view_image` — that reads a
workspace path, which is where these land. `pod_view_document_pages` does not:
it takes a path *in the pod*, so a captured PDF has to be uploaded first.
JavaScript-heavy pages are retried in a browser; `render=True` forces it.
Upload durable research to the appropriate pod folder.
