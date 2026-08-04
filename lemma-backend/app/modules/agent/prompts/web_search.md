## Web Search

Use web search when the task needs current or external information.

```bash
lemma tools web-search "query terms" --limit 5
save-webpage https://example.com/article --formats markdown,pdf,jpeg --out research
```

`web-search` returns URLs and snippets. Use `save-webpage` when the rendered page, markdown, PDF, or a screenshot matters — then upload durable research artifacts to `/me/...` (or a shared top-level folder) so they can be retrieved later.
