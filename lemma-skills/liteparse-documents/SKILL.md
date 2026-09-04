---
name: liteparse-documents
description: "Use this skill to parse documents that live OUTSIDE the pod's file system — a PDF an agent fetched from the web, a local one-off, or any file you won't upload — or as a fallback when a pod file lacks its auto-produced markdown/page-images. Extracts text, layout, bounding boxes, OCR, page screenshots, or structured output from PDF, DOCX, PPTX, XLSX, CSV, TSV, images, and other LiteParse formats. For documents that live in the pod, prefer the pod's built-in conversion/search instead."
---

# LiteParse Documents

Use LiteParse (`lit`) to parse documents that **aren't in the pod** — a PDF an agent pulled from the web, a local scratch file, anything you won't upload — when you need text, spatial layout, OCR, bounding boxes, or page screenshots. It's also the **fallback** for a pod file whose auto-produced markdown/page-images are missing or insufficient (scanned/OCR, bounding boxes). For a document that lives in (or is going into) the pod, prefer the pod's built-in conversion and search — see below.

## When `lit` vs. the pod's built-in processing

Decide by **where the document lives** — that's the first question, not an afterthought:

- **It's a pod file (or going into the pod) → use the pod, not `lit`.** `lemma files upload <file> /knowledge/<name>` and the pod **auto-converts and indexes** it: semantic+keyword search (`lemma files search`), page-marked markdown (`lemma files cat --pages`), rendered page images and figures (`lemma files child …/pages/page_0001.jpg`) — no local parsing. The pod *is* the RAG system; don't re-implement extraction for documents you're putting there. (See `lemma-builder/references/files.md` and the `lemma-user` skill.) The auto-conversion is an allow-list — PDF, DOC/DOCX, ODT, RTF, Markdown, plain text, HTML, EPUB — so uploading a **spreadsheet, presentation, image or email** stores the bytes and produces nothing to read (`lemma files stat` reports `NOT_REQUIRED`). Those are `lit`'s lane even when they live in the pod: download and parse locally.
- **It's outside the pod, or the pod artifact is missing/insufficient → use `lit`.** A PDF fetched from the web, a local file you won't upload, a scanned PDF needing OCR, bounding-box/layout extraction, or screenshotting pages to decide what's worth keeping — and the **fallback** when a pod file lacks its derived markdown/images. That's LiteParse's lane.

Common flow: `lit screenshot` or `lit parse` an outside file to inspect it, then `lemma files upload` the ones worth keeping so the pod converts and indexes them.

## Tooling

The workspace image installs `lit` (and the `liteparse` alias) from
`@llamaindex/liteparse`, alongside Node and a headed Chromium. That is what you
can count on:

```bash
lit --help
```

**Everything else is a probe, not a promise.** LibreOffice, ImageMagick and
Tesseract language data are *not* installed by the workspace image, and nothing
sets `$TESSDATA_PREFIX`. Some runtimes inherit them from their base image and
some do not, so check before you plan around them rather than after a parse
fails:

```bash
command -v libreoffice soffice magick convert tesseract
echo "${TESSDATA_PREFIX:-<unset>}"
```

When one is missing, say so and take the other route rather than trying to
install it: an Office file can go through `lemma files upload` and be read back
as the pod's converted markdown, and a scanned PDF can be rendered with
`lit screenshot` and read with the image-viewing capability instead of OCR.

## Workflow

1. Identify the file type and the desired output: plain text, JSON with bounding boxes, or page screenshots.
2. For searchable text or layout extraction, run `lit parse`.
3. For visual inspection, charts, scans, handwriting, dense tables, or agent vision workflows, run `lit screenshot`.
4. Save generated outputs beside the source file or in a clearly named working directory, then inspect the result before relying on it.
5. When a file is scanned or image-heavy, keep OCR enabled. Use `--no-ocr` only when the user wants embedded text only or speed matters more than recall.

## Common Commands

Parse to text:

```bash
lit parse input.pdf -o output.txt
```

Parse to JSON with bounding boxes:

```bash
lit parse input.pdf --format json -o output.json
```

Parse selected pages:

```bash
lit parse input.pdf --target-pages "1-5,10" --format json -o output.json
```

Parse Office documents or images:

```bash
lit parse input.docx --format json -o output.json
lit parse input.png --format json -o output.json
```

Generate screenshots:

```bash
lit screenshot input.pdf --target-pages "1-3" --dpi 200 -o screenshots
```

Batch parse a directory:

```bash
lit batch-parse input-directory output-directory --recursive --format json
```

## Output Guidance

- Use text output for quick reading, summarization, search, or simple extraction.
- Use JSON when downstream code needs bounding boxes, page numbers, or structured blocks.
- Use screenshots when text extraction may miss visual relationships, tables, signatures, charts, diagrams, or scanned content.
- For large documents, start with `--target-pages` or `--max-pages` to avoid unnecessary processing.
- Do not claim perfect table, handwriting, or chart extraction from LiteParse alone. For complex visual documents, combine screenshots with model vision or tell the user that a heavier parser may be needed.

## Troubleshooting

- If Office files fail to parse, check `libreoffice --version` (or `soffice`). The workspace image does not install it; when it is absent, upload the file and read the pod's converted markdown instead.
- If image inputs fail, check `magick --version` or `convert --version`. Same story — not installed by the workspace image.
- OCR needs Tesseract and its language data, neither of which the workspace image installs. If `tesseract --version` works, pass `--ocr-language <lang>` and make sure the matching `.traineddata` file exists in `$TESSDATA_PREFIX`. If it does not, render pages with `lit screenshot` and read them with the image-viewing capability rather than reaching for OCR.
- If the document is password protected, use `--password <password>` only when the user has provided the password.

## See also

- Upload + the pod's auto-index/search/markdown/page-images → `lemma-builder/references/files.md`
- Operate pod files from the CLI (search, cat, child, view-image) → the `lemma-user` skill
