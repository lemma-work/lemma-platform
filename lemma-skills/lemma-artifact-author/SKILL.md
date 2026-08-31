---
name: lemma-artifact-author
description: "Create, revise, and deliver polished, durable artifacts in a Lemma workspace: Markdown, HTML, DOCX, PDF, XLSX, and PPTX. Use for reports, documents, workbooks, presentations, exports, handouts, template-based revisions, or publication-ready files that must preserve source fidelity, be rendered and visually verified, and be uploaded to private /me or a shared pod folder. Route deployed Lemma apps to lemma-app-design and lemma-builder instead."
---

# Lemma Artifact Author

Create the requested native file, prove that its content and rendering are sound,
and place the verified deliverable where people can actually use it. Treat the
editable source, any fixed-layout export, and the pod copy as one delivery chain.

## Hold the quality line

- Preserve every source and template unchanged. Work on a copy in an isolated
  temporary directory or a clearly named workspace output directory.
- Preserve meaning and data, not merely appearance. Never invent facts, silently
  change numbers, flatten formulas, discard notes, accept tracked changes, or
  remove advanced document features without authorization.
- Produce the requested format with a tool that genuinely writes that format.
  Never rename an HTML, text, or PDF file to an Office extension.
- Inspect rendered output. A successful write, conversion, or archive-open check
  does not prove that a document looks correct.
- Upload only the verified final files. Never leave the sole deliverable in a
  local temp path.
- State unverified properties and tool limitations plainly. Do not claim visual,
  accessibility, formula, or round-trip QA that did not run.

## 1. Define the delivery contract

Resolve from the request and available context:

1. Identify the audience, decision or job, required format, delivery deadline,
   and expected level of polish.
2. Identify authoritative sources, supplied templates, brand constraints,
   required citations, and elements that must remain byte- or layout-faithful.
3. Decide whether the user needs an editable native file, a distribution copy
   such as PDF, or both. Keep an editable source beside a derived PDF unless the
   user explicitly wants only the PDF.
4. Choose the durable pod destination: `/me/...` for the invoking user's private
   copy; another top-level path such as `/reports/...` for a pod-shared copy.
   Never invent a `/pod` prefix or upload to an unconfirmed pod.
5. Define acceptance checks before authoring: required sections, record counts,
   control totals, page/slide limits, viewports, accessibility needs, and any
   source-to-output invariants.

Ask only when an unresolved choice would materially change content, access, or
format. Otherwise make a conservative assumption and record it.

## 2. Acquire sources without degrading them

Orient to an active pod before reading or publishing:

```bash
lemma pods list
lemma pods describe
```

For pod sources, use Lemma's native file layer first:

```bash
lemma files stat /reports/source.docx
lemma files cat /reports/source.docx --pages 1-5
lemma files download /reports/source.docx ./source.docx
```

Use `cat`, search, and child page images to understand an indexed pod document;
download exact original bytes only when native editing or fidelity comparison
requires them. Do not re-parse a pod document that already has converted
markdown and rendered pages. Use `lemma-user` for the full file workflow and
`liteparse-documents` only for outside files or missing/insufficient pod-derived
artifacts.

Record the source path or URL, version/date, and relevant page, sheet, slide, or
range. Keep facts traceable to those sources. If the source is mutable, capture a
working snapshot before editing.

## 3. Discover tooling, then route the format

Inspect the environment's available artifact capabilities, bundled dependency
runtime, project manifests, and installed binaries before selecting a toolchain.
Verify the actual writer, reader, renderer, and version you intend to use. Do not
assume a package, office suite, font, browser, or converter is installed, and do
not add dependencies or alter project manifests unless the task authorizes it.

Prefer, in order:

1. A native format-specific capability that supports creation or revision plus
   rendering and reopening.
2. A bundled workspace library or installed application confirmed at runtime.
3. A loss-aware conversion path with the editable source retained.
4. A clearly labelled fallback in a format the environment can produce.

Do not counterfeit the requested extension. If the available path would drop
material features or materially change the requested deliverable, explain the
constraint and obtain direction before proceeding.

Read [references/formats.md](references/formats.md) before editing or creating the
selected format. Read only the relevant format sections.

## 4. Author or revise from a content model

Build an outline, slide map, workbook model, or page structure before styling.
Use real content and representative data; do not leave placeholder copy unless
the user requested a template. Keep that model the thing you edit: change
generated output by regenerating it, not by patching the rendered file with
`sed -i` or another regex-substitution tool.

For revisions, make the smallest defensible change in the native source. Preserve
styles, section and sheet structure, references, metadata, embedded assets, and
unsupported features. Compare source and output counts and invariants after the
edit. When a native revision path is unsafe, produce a separate proposed revision
or a new version instead of corrupting the original.

For source-backed work, keep citations adjacent to claims, label estimates and
assumptions, preserve units and precision, and distinguish missing values from
zero. Use a stable, descriptive filename; avoid ambiguous chains such as
`final-v2-really-final`.

## 5. Run structural, visual, and fidelity QA

Use a real render-and-revise loop:

1. Reopen the produced file with an independent reader or the same tool in read
   mode. Confirm it is non-empty, parseable, and structurally complete.
2. Run format-specific programmatic checks: sections and links, page/slide/sheet
   counts, formulas and error values, embedded assets, expected headings, control
   totals, citations, and metadata.
3. Render every document page and slide, every relevant workbook sheet or print
   range, and representative HTML desktop, mobile, and print views. Inspect an
   overview montage, then inspect dense or suspicious regions at readable size.
4. Fix clipping, overflow, overlap, orphaned headings, broken page breaks, font
   substitution, unreadable labels, weak contrast, bad image crops, formula
   errors, and unexpected blank output. Re-render after each material change.
5. Compare the final values, text, source references, and counts with the source.
   Recalculate independently for high-stakes totals where practical.
6. Check accessibility supported by the target format and tool: semantic heading
   order, document title and language, useful alt text, table headers, readable
   link text, logical reading order, sufficient contrast, and charts that do not
   rely on color alone.

If rendering is unavailable, use every safe structural check available, mark
visual QA as incomplete, and do not describe the artifact as visually verified.

## 6. Publish the verified files to the pod

Keep working files local until the acceptance checks pass. Then upload the native
source and requested exports to the selected destination:

```bash
lemma files mkdir /reports
lemma files upload ./quarterly-review.pptx /reports/quarterly-review.pptx
lemma files upload ./quarterly-review.pdf /reports/quarterly-review.pdf
lemma files stat /reports/quarterly-review.pdf
lemma files url /reports/quarterly-review.pdf
```

Use `/me/<folder>/...` instead for a private deliverable. `stat` reports indexing
state: wait for `COMPLETED` before claiming an indexable prose document is
searchable; `NOT_REQUIRED` is expected for stored binary/data formats such as
XLSX. Treat `FAILED` as a delivery defect, not success.

For high-stakes delivery, download the exact uploaded bytes to a fresh temporary
path, compare size or hash with the verified local final, and reopen that copy.
Use `lemma files url` for a signed-in pod-member link. Create a public
`lemma files share` link only when the user explicitly needs external access.

Remove disposable previews and conversion intermediates that you created after
verification when safe. Preserve the verified local final when it is useful for
handoff or recovery.

## Completion gate

Do not finish until you can report:

- the final native format and any derived exports;
- the authoritative sources and preserved original/template;
- the structural, visual, data-fidelity, and accessibility checks actually run;
- the exact local final path and durable `/me` or shared pod path;
- the member link when useful; and
- any remaining limitation or unverified property.

Use `lemma-research` or `lemma-data-analysis` for the investigation or analytical
reasoning when needed; use this skill to turn that work into a durable, verified
deliverable. Use `lemma-app-design` and `lemma-builder` for a deployed Lemma app
rather than treating the app as an HTML report.
