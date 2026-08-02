# Format routing and preservation checks

Read the section for each requested source and output format. Prefer the requested
native format; use companion formats only when they add real editing,
distribution, or accessibility value.

| Target | Best fit | Keep beside it when useful | Required visual proof |
|---|---|---|---|
| Markdown | Text-first, versionable, searchable writing | Referenced images/data | Rendered Markdown or HTML preview |
| HTML | Responsive report, interactive explainer, printable web document | Local assets or print PDF | Desktop, mobile, and print views |
| DOCX | Editable long-form business document | PDF distribution copy | Every rendered page |
| PDF | Fixed-layout distribution, print, or archival copy | Editable native source | Every rendered page |
| XLSX | Calculations, structured data, models, and editable tables | Source data and a PDF/PNG summary if requested | Every relevant sheet/range and chart |
| PPTX | Editable live presentation | PDF handout if requested | Every rendered slide |

## Markdown

- Use a logical heading hierarchy, short sections, descriptive links, fenced code
  languages, and tables only when they remain readable at the expected width.
- Preserve source citations as durable links or footnotes. Keep image paths and
  attachments portable relative to the final file when packaging locally.
- Validate links, anchors, code fences, table structure, and image references.
- Render the same Markdown flavor the destination uses when that renderer is
  known. Inspect long lines, nested lists, wide tables, and callouts.
- Prefer Markdown for the pod's durable searchable report when rich page layout
  is not a requirement.

## HTML

- Decide whether delivery is one self-contained file or a documented file plus
  asset directory. Never leave local absolute paths, temporary URLs, secrets, or
  build-machine dependencies in the final.
- Use semantic landmarks, one meaningful `h1`, ordered headings, labelled controls,
  keyboard-operable interactions, visible focus, useful alt text, and sufficient
  contrast. Mark decorative images as such.
- Make layout responsive without horizontal scrolling at the target widths. Add
  print styles when the document will be printed or exported to PDF.
- Keep JavaScript purposeful. Provide a readable static state if scripting fails,
  and avoid remote dependencies unless the user accepts their availability and
  privacy tradeoffs.
- Validate DOM structure and links, then render desktop, narrow mobile, and print
  output. Exercise interactions, overflow states, long content, and empty states.
- Route an authenticated or data-backed Lemma app to `lemma-app-design` and
  `lemma-builder`; keep this lane for reports and document-like HTML.

## DOCX

- Revise a copy of the native document. Preserve page size, margins, sections,
  styles, numbering, headers, footers, page breaks, fields, citations, footnotes,
  comments, tracked changes, tables, images, captions, and content controls unless
  the requested change requires otherwise.
- Use named styles and a real heading hierarchy instead of manual formatting.
  Keep table header rows, sensible column widths, non-color cues, alt text, title,
  language, and logical reading order where the writer supports them.
- Do not silently accept or reject tracked changes, unlink fields, flatten charts,
  substitute fonts, or strip unsupported embedded content. If the tool cannot
  round-trip a feature, leave the source untouched and use a separate revision or
  a documented fallback.
- Reopen the DOCX and compare paragraph, table, image, section, comment, and
  revision counts when relevant. Render every page and inspect wrapping, widows,
  orphans, page breaks, headers/footers, tables, and image crops.
- Generate PDF from the final DOCX only after native QA; verify the PDF separately.

## PDF

- Treat PDF as the fixed-layout final and retain its editable source. Edit the
  native source and regenerate whenever possible instead of patching the PDF.
- Determine whether the source is born-digital, scanned, fillable, annotated,
  password-protected, or digitally signed. Never alter a signed PDF and imply the
  signature remains valid; preserve it and create a clearly separate derivative.
- Preserve searchable text, selectable links, bookmarks, page labels, annotations,
  form behavior, fonts, page boxes, and metadata when they are part of the job.
  Preserve original scan images even when adding an OCR text layer.
- Check file integrity, page count, page dimensions/orientation, text extraction,
  link targets, font substitution, blank pages, and forms where applicable.
- Render every page. Inspect overview thumbnails plus dense pages at full size for
  clipping, overlaps, unexpected reflow, rasterization, weak contrast, and print
  margins. Check tags and reading order only with tooling that can actually expose
  them; otherwise report accessibility tagging as unverified.

## XLSX

- Keep formulas as formulas and values in their correct cell types. Preserve sheet
  order, tables, named ranges, filters, freeze panes, validations, conditional
  formats, number formats, charts, comments, links, hidden sheets, and protection
  unless the task requires a change.
- Never overwrite formulas with cached display values. Never coerce identifiers
  with leading zeros into numbers. Preserve dates, time zones, units, currencies,
  percentages, precision, blanks, and error states deliberately.
- Separate inputs, calculations, and outputs when creating a model. Label units,
  assumptions, sources, refresh date, and editable cells. Avoid hard-coded numbers
  inside formulas when a labelled assumption cell is clearer.
- Recalculate with an available spreadsheet engine when possible. Check expected
  formula coverage and scan for errors such as `#REF!`, `#DIV/0!`, `#VALUE!`,
  `#NAME?`, and broken external links. Reconcile row counts, unique keys, control
  totals, and sampled values against the source.
- Reopen the workbook after saving. Render every user-facing sheet and relevant
  print range, plus every chart. Inspect truncation, widths, row heights, merged
  cells, hidden content, filters, page breaks, print areas, legends, axes, units,
  and labels. Do not use CSV as an editing round-trip for a formatted workbook.

## PPTX

- Preserve slide size, theme, masters, layouts, fonts, notes, comments, hyperlinks,
  groups, animations, transitions, media, and hidden slides unless the task says
  otherwise. Work from the supplied template rather than imitating it.
- Build one clear idea per slide with a deliberate hierarchy, consistent grid,
  real content, concise copy, and visuals that carry meaning. Keep sources and
  definitions close to the claims they support.
- Use slide titles, meaningful reading order, sufficient contrast, non-color cues,
  alt text, and captions where supported. Keep speaker notes intact during edits.
- Reopen the deck and compare slide, notes, media, and hidden-slide counts when
  relevant. Check for font substitution, off-slide objects, broken links, and
  missing media.
- Render every slide. Inspect the full contact sheet for rhythm and consistency,
  then inspect dense slides at full size for overflow, overlaps, alignment, image
  crops, tiny labels, and unreadable footnotes. Verify any handout PDF separately.

## Loss-risk rule

Treat macros, digital signatures, advanced fields, embedded objects, external
links, complex animations, forms, accessibility tags, and password protection as
high-risk round-trip features. Confirm tool support before editing. If support is
uncertain, preserve the original, make no destructive in-place change, and name
the limitation in the handoff.
