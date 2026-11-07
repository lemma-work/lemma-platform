# Files in detail

The commands are in `SKILL.md`. This is the rest: what gets indexed, what a
document's derived artifacts are, the read caps, indexing states, and links.

## What is indexed

PDF, DOC, DOCX, ODT, RTF, Markdown, text, HTML and EPUB are extracted, chunked,
embedded and converted to page-marked markdown on upload. That list is the whole
of it. Spreadsheets and tabular data (CSV, TSV, JSON, YAML, XLSX, ODS),
presentations (PPTX, ODP), images (no OCR), email (`.eml`, `.msg`), audio, video
and archives are stored but never indexed, so they never appear in search.

Read a document's derived artifacts rather than parsing the file again. Use the
`liteparse-documents` skill (`lit`) only for a document from outside the Lemma,
or when a derived artifact is missing or not enough (scans, bounding boxes).

## Search

`--method` is `HYBRID` (default), `VECTOR` (semantic) or `TEXT` (keyword).
`--scope` searches a folder and everything beneath it; `--direct` narrows that
to the folder's immediate children and does nothing without `--scope`. Results
are ranked passages with page numbers, ready for `cat --pages N`.

## Reading

`cat --mode` is `auto` (default: raw text for `.md`/`.txt`, converted markdown
for documents), `text` (raw bytes; binary is flagged, not dumped) or `markdown`
(errors if the file is not a document). Output is capped at about 50,000
characters, the same as the in-process tool. Widen with `--max-chars 0`
(unlimited) or `--full`; `--max-lines N` and `--max-tokens N` only cap it further; narrow with
`--pages` (1-based, over the converted markdown) or `--lines` (1-based, over raw
text). The payload reports `page_count`, the range returned and a `truncated`
flag, so you know when to page.

## Derived artifacts

A processed document has hidden children at `<file-path>/<artifact>`:

- `…/document.md`: page-marked converted markdown (`<!-- PAGE n -->`)
- `…/pages/page_0001.jpg` …: rendered page images, 1-based
- `…/image_0.png` …: extracted figures, at the container root (not under `images/`)

To see a page (a chart, a signature, a scanned form), fetch its image with
`files child` and view it. To read what it says, `files cat … --pages N`.

In a Lemma, `view_image` takes exactly one of `pod_file_path` (no download) or
`workspace_file_path`; it never guesses the store from the path, and both or
neither is an error. It takes images only. For a PDF use
`pod_view_document_pages`, which renders the pages directly. It works on any
vision-capable model whatever toolsets are configured.

## Indexing states

`files stat` reports one of:

- `COMPLETED`: searchable.
- `NOT_REQUIRED`: stored, not an indexed type.
- `PENDING` / `PROCESSING`: still working, including a failed attempt waiting
  for a retry.
- `FAILED_PERMANENT`: out of retries or unprocessable; the reason is in
  `last_processing_error`. Nothing re-drives it. New bytes, or
  `lemma files retry <path>` once the cause is fixed, reopens it. Uploading
  the same bytes again does not.

`files append` is read-modify-write: the last writer wins.

## Links

`files url` returns an `app_url` (opens in the app, for someone signed in to the
Lemma) and a short-lived download `url`. `files share` mints a public link that
needs no sign-in. Send the public link outside the Lemma; point people inside it
at `app_url`.

A public link expires after `--ttl` (`30m`, `3h`, `7d`; default 24h, max 7d) and
stops after `--max-hits` downloads (default 200, max 1000). A value outside
either range is refused, not clamped. The cap is a byte budget, that many whole
copies of the file: a revalidation (304) or a link-preview HEAD costs nothing,
and a ranged read costs only the bytes it moves.

`files shares` lists the links you minted, newest first, and follows the pages
for you (`--all` adds expired and revoked ones). `files unshare <code>` revokes
one now; the code is the link's last path segment. Both see only links you
minted on files you can still read.

You may hold 500 live links per Lemma. Past that, minting answers 429 with how
many are live. Revoking, expiry or a used-up download cap frees a slot at once.

In a function or agent: `pod.files.get_url(path)`,
`pod.files.create_signed_url(path, …)`, `pod.files.list_signed_urls()`,
`pod.files.revoke_signed_url(code)`.
