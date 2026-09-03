# Files

Files are the pod's document system, asset store, and **retrieval layer** — not
passive blobs. Upload a document and it is automatically extracted, chunked,
embedded, and indexed: **the pod *is* the RAG system**, no vector DB to stand up.
Document-like files also expose derived **child files** (converted markdown +
figures + page renders) for full-document reading and page-accurate viewing.

> Grounds in `pod-model.md` (the file layer). This doc is the build + CLI view; the
> `lemma-user` skill is the operator view of the same commands.

## The model, for files

- **Two areas.** `/me/...` is the user's **private** tree (resolves to that user's
  own subtree; owner-only). Everything else is **pod-shared** — top-level folders
  like `/knowledge`, `/contracts` (there is **no** `/pod` prefix — a path is shared
  unless it's under `/me`). **Folder grants cascade**: granting `/knowledge` grants
  every file and subfolder beneath it.
- **Only documents are indexed.** PDF, DOC/DOCX, ODT, RTF, Markdown, plain text,
  HTML, EPUB are extracted + searchable. Data/binary (CSV, TSV, JSON, YAML, XLSX,
  images, EML/MSG) are stored and downloadable but **never indexed** — they don't
  appear in search. Rule of thumb: **prose/documents → files (searchable);
  structured data → tables.**
- **Delegated `/me`.** A function or agent runs as the **invoking user**, so its
  `/me` is *that user's* tree. There is no workload-private space. Workloads have
  **zero ambient access** to pod-shared folders — grant each folder they touch.

## A natural filesystem (CLI)

`lemma files …` (singular `lemma file` is the same command). `ls`/`tree` default to
`/me`.

```bash
lemma files mkdir /knowledge
lemma files upload ./handbook.pdf /knowledge/handbook.pdf      # documents auto-index
lemma files upload ./summary.md /me/reports/summary.md
lemma files upload ./data.csv /scratch/data.csv --no-search    # skip indexing

lemma files ls /knowledge
lemma files tree /
lemma files stat /knowledge/handbook.pdf        # metadata incl. indexing status

lemma files write /me/notes/draft.md "first line"   # create/overwrite (or pipe via stdin)
lemma files append /me/notes/draft.md "next line"   # append (read-modify-write)
lemma files mv /me/notes/draft.md /me/notes/final.md
lemma files rm /scratch/data.csv
```

## Reading documents — `cat` is page- and mode-aware

```bash
lemma files cat /knowledge/handbook.pdf                 # auto: text for .md/.txt; converted markdown for PDF/DOCX/…
lemma files cat /knowledge/handbook.pdf --pages 3-7     # 1-based, inclusive, over the converted markdown
lemma files cat /me/notes/log.md --lines 10-50          # 1-based line slice over raw text
lemma files cat /knowledge/handbook.pdf --mode markdown # force converted markdown (errors if not a document)
lemma files cat /scratch/data.csv --mode text           # raw bytes (binary → flagged, not dumped)
```

Output is capped at ~50,000 chars by default (matches the in-process agent tool);
override with `--max-chars 0` (unlimited), `--max-lines N`, `--max-tokens N`, or
`--full`. The payload reports `page_count`, the returned range, and a `truncated`
flag so you know when to page.

```bash
lemma files download /knowledge/handbook.pdf ./handbook.md --markdown   # save converted markdown
lemma files download /knowledge/handbook.pdf ./handbook.pdf             # exact original bytes
```

## Search — semantic, keyword, hybrid; folder-scoped

```bash
lemma files search "termination clause" --scope /contracts            # HYBRID, folder + all subfolders (SUBTREE)
lemma files search "refund window" --scope /policies --method VECTOR --direct
```

Results are ranked passages **with page numbers** — so you can jump straight to
`cat … --pages N`. `--scope` + the default SUBTREE (folder and everything beneath)
is your retrieval design: group a knowledge base under one folder and scope an
agent's reads to it. `--method` is `HYBRID` (default), `VECTOR` (semantic), or
`TEXT` (keyword); `--direct` limits to immediate children.

## Derived child files (page-accurate reading & viewing)

A processed document exposes hidden child artifacts at `<file-path>/<artifact>`:

- `…/handbook.pdf/document.md` — page-marked markdown (`<!-- PAGE n -->`; `--pages` slices it)
- `…/handbook.pdf/image_0.png` — extracted figures, referenced inline (no `images/` subfolder; only pages get one)
- `…/handbook.pdf/pages/page_0001.jpg` — rendered page images (1-based)

```bash
lemma files children /knowledge/handbook.pdf                          # list them
lemma files child /knowledge/handbook.pdf/pages/page_0003.jpg ./p3.jpg # fetch one
lemma files child /knowledge/handbook.pdf/document.md --pages 3-7      # markdown page range
```

**View pod files as images.** The rendered page JPEGs (and uploaded images) are
exactly what the **view-image** capability reads — fetch a page with `files child`
(or get a URL with `files url`) and view it to *see* a chart, a signature, a scanned
form, layout. This also applies to **workspace** files. So for "what does page 3
look like?" → `files child …/pages/page_0003.jpg` → view-image; for "what does it
say?" → `files cat … --pages 3`.

## Links — pick by who opens it

```bash
lemma files url /reports/summary.pdf                  # app_url (in-app, signed-in member) + short-lived download url
lemma files share /reports/summary.pdf --ttl 3h --max-hits 50   # public, no-login, expiring + hit-capped
```

`--ttl` = `30m`/`3h`/`24h` (default 3h, max 24h); `--max-hits` caps downloads
(default 50, max 100). The public link streams through the backend and stops at the
cap — both bounds are clamped server-side. Folders have no URL.

## Tables + Files pattern

Never store a document body in a record. Store the **path** + structured state:

```json
[
  { "name": "file_path", "type": "FILE_PATH", "required": true, "max_length": 700 },
  { "name": "review_status", "type": "ENUM", "options": ["new", "extracting", "review", "approved"], "default": "new" },
  { "name": "summary", "type": "TEXT" },
  { "name": "extracted", "type": "JSON" }
]
```

Pipeline: upload to `/inbox` → row with `file_path` + status → function/agent reads
converted markdown, writes `extracted` + `summary`, flips status → app shows the
queue. (App side: `app-recipes/file-viewer.md`.)

## From functions and agents

```python
pod.files.search("refund policy")                              # indexed chunks (with pages)
md  = pod.files.download_markdown("/knowledge/policy.pdf")     # converted markdown bytes (page-marked)
pg  = pod.files.download_child("/knowledge/policy.pdf/pages/page_0003.jpg")   # a page image (bytes)
raw = pod.files.download("/knowledge/policy.pdf")             # exact bytes
pod.files.upload("/tmp/summary.md", directory_path="/reports")
pod.files.write_text("/me/notes/draft.md", "first line")
urls = pod.files.get_url("/knowledge/policy.pdf")             # .app_url + short-lived .url
link = pod.files.create_signed_url("/reports/summary.pdf", expires_seconds=3600, max_hits=50)
```

`/me` here is the **invoking user's** tree. Grant each pod folder the workload
touches: `resource_type: "folder"`, `resource_name: "/knowledge"` (the stored path),
`permission_ids: ["folder.read"]` (add `folder.write` for uploads). Tell agents in
their instructions that pod files are **searchable by path and fully readable via
converted markdown** — otherwise they assume they only get search snippets.

## Limits & gotchas

- **Bundles carry folder metadata only** (`files/<folder>/.folder.json`); file
  bytes upload separately (`lemma files upload`) — script them in `seed/` and the
  README.
- Indexing lags briefly after upload — `stat` shows status (`COMPLETED` = searchable,
  `NOT_REQUIRED` = stored but not a document, `PENDING`/`PROCESSING`/`FAILED`).
- Don't expect markdown/search for XLSX/CSV/JSON/email/images — these are stored, not
  indexed; keep structured data in tables, or parse a one-off with the python or
  `liteparse-documents` skill (its lane is documents **outside** the pod, plus the
  fallback when a pod file has no derived markdown/images).
- `append` is read-modify-write (last writer wins); not concurrency-safe.

## Verify

```bash
lemma files upload ./sample.pdf /knowledge/sample.pdf
lemma files stat /knowledge/sample.pdf               # wait for COMPLETED
lemma files search "<phrase from the file>" --scope /knowledge
lemma files cat /knowledge/sample.pdf --pages 1      # converted markdown, page 1
lemma files child /knowledge/sample.pdf/pages/page_0001.jpg ./p1.jpg   # then view-image p1.jpg
```

## See also

- The model → `pod-model.md` · structured data → `tables.md`
- Files in an app → `app-recipes/file-viewer.md` · operate → the `lemma-user` skill
