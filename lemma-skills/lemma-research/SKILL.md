---
name: lemma-research
description: "Run rigorous, source-backed research in an existing Lemma pod. Use for investigations, literature or market scans, policy and product research, fact-checking, comparisons, current-information questions, and updates to prior research that require pod files, workspace material, or web sources; exact citations; explicit freshness and conflict handling; and a durable memo, evidence ledger, or source pack published to /me or an authorized shared pod folder."
---

# Lemma Research

Build an auditable investigation, not a disposable answer. Preserve the path from
question to source to evidence to claim, then publish the useful result into the
pod when the task authorizes a durable artifact.

For any multi-source investigation, read
[`references/investigation-format.md`](references/investigation-format.md) before
collecting evidence. Use its stable IDs, ledger fields, citation forms, and memo
structure.

## Operating contract

- Ground every material factual claim in inspected evidence. Treat search-result
  snippets, filenames, summaries, and model memory as leads, not evidence.
- Separate **source** (`S##`), **evidence** (`E##`), and **claim** (`C##`). Preserve
  the distinction even in a short answer.
- Prefer pod-local context before external context when researching the user's
  organization, history, or prior work. Do not assume the public web supersedes
  private canonical material.
- Act only as the current user. Respect pod membership, workload grants, RLS, and
  file visibility. A delegated agent uses the invoking user's RLS scope and
  `/me`; it gains no service-account or admin view. Its authority is its own
  grants **intersected with** what the invoking user could do — never their
  union — so a grant can only narrow the investigation's reach, never widen it
  past the person you are acting for.
- Treat an empty result as "not visible or not found in this scope," not proof
  that the information does not exist. Never bypass RLS or a missing grant.
- Keep observable research records, not hidden chain-of-thought. Record queries,
  sources, evidence, decisions, conflicts, and unknowns; omit private reasoning
  traces.
- Use current CLI help or the `lemma-user` skill if a command differs. Never
  improvise a Lemma command or path.

## 1. Frame the investigation

Orient to the active pod and its existing knowledge layout:

```bash
lemma pods describe
lemma files ls /
```

Write a compact research contract before searching:

1. State the decision or question the result must support.
2. Define the subject, population, geography, time window, comparison set, and
   ambiguous terms.
3. State what is out of scope and what deliverable is expected.
4. Decompose the question into answerable subquestions. Classify each as factual,
   comparative, causal, interpretive, or forward-looking so the evidence standard
   matches the claim.
5. Identify the claims that would change the conclusion, plus the strongest
   plausible counterclaim for each.
6. Set an `as_of` date and a stopping rule: stop when decisive claims have adequate
   support, credible contradictions are resolved or exposed, and remaining
   unknowns are explicit.

Do not silently broaden the task. Ask only when a missing choice would materially
change the research; otherwise state the narrow assumption and proceed.

For an update to prior research, locate the existing brief, source register,
evidence ledger, and memo first. Preserve stable IDs, add new source versions,
mark superseded or stale claims, and report the delta instead of silently replacing
the earlier record.

## 2. Design the source strategy

Map every subquestion to its best available evidence, required freshness, and a
fallback source. Match source type to claim type instead of applying one universal
ranking.

Use this order where relevant:

1. Inspect user-provided and pod sources for the user's facts, prior decisions,
   terminology, and internal state.
2. Inspect in-scope workspace files for implementation truth, datasets, logs, and
   authored material.
3. Search the web for current or external facts, beginning with original records,
   official documentation, first-party data, standards, filings, or research.
4. Use reputable secondary sources for discovery, interpretation, and independent
   corroboration.
5. Search deliberately for disconfirming evidence, superseding versions, and
   definitions that could make apparently similar numbers incomparable.

Do not count duplicated reporting as independent corroboration. Trace syndication
and citations back to the originating source where possible.

## 3. Retrieve and inspect sources

### Pod files

Search narrowly, then open the exact passage:

```bash
lemma files search "target concept" --scope /knowledge
lemma files search "exact phrase" --scope /knowledge --method TEXT
lemma files cat /knowledge/source.pdf --pages 3-7
lemma files cat /me/notes/context.md --lines 20-80
```

Use `HYBRID` search by default; use `TEXT` for exact names, identifiers, and quoted
phrases, and `VECTOR` for conceptual recall. Vary queries and scoped folders rather
than trusting one ranked result. Record the path and returned page or line range.

Inspect a chart, scan, signature, footnote, table, or layout visually instead of
inferring it from extracted text:

```bash
lemma files children /knowledge/source.pdf
lemma files child /knowledge/source.pdf/pages/page_0003.jpg ./source-p3.jpg
```

Then view `./source-p3.jpg` with the available image-viewing capability. Use
`lemma files cat ... --pages 3` for what the page says and the rendered page image
for what it shows. Prefer the pod's converted markdown and page images; use
`liteparse-documents` only for outside-pod documents or when the derived artifacts
are missing or insufficient.

Remember that spreadsheets (CSV, TSV, JSON, YAML, XLSX, ODS), presentations
(PPTX, ODP), images, and email files are stored but not indexed. Only
successfully processed, search-enabled documents with extracted chunks appear in
search; a stored non-indexed file does not appear by filename alone. Check
`lemma files stat <path>`, then list or download known files instead of concluding
that search found everything. `NOT_REQUIRED` means the file was never eligible;
`PENDING`/`PROCESSING` means retry shortly; `FAILED_PERMANENT` means retrying
will never help and the document has to be read another way.

### Workspace files

Inventory before reading broadly:

```bash
rg --files ./path/to/in-scope-material
rg -n "target term" ./path/to/in-scope-material
```

Use repository files, schemas, tests, logs, and history as direct evidence only for
what they actually establish. Record an absolute or repository-relative path plus
line, symbol, revision, or timestamp. Do not upload private workspace material to
a shared pod folder unless the task authorizes that audience.

### Web sources

Discover sources with your own web search — phrase the query specifically, with a
date or a domain, rather than broadly. There is no CLI search command: search is a
capability you already have, and the CLI's wrapper around it was removed.

Open promising results and inspect the source itself. Use the browser for
authentication, form navigation, or anything you have to interact with. Preserve
a decisive or volatile page when the investigation needs a durable snapshot;
which command depends on what you have:

- With the `WEB_SEARCH` toolset, `web_fetch` is the direct route and needs no
  shell. It takes a **list** of up to 5 URLs in one call, writes each page to
  `out_dir` in the workspace, and returns the file paths and a short preview
  rather than the page text. `formats` defaults to `["markdown"]`; add `pdf`,
  `jpeg` or `png` when the layout is the evidence (a chart, a table, a signed
  page) and view the result with the image-viewing capability. Set `render: true`
  for pages that build their content with JavaScript, or when a plain fetch came
  back near-empty. Pages reported as not attempted should be re-requested on
  their own, not by repeating the whole list.
- With a workspace shell, `save-webpage` does the same through the agent browser:

```bash
save-webpage https://example.com/source --formats markdown,pdf --out research
```

Record the canonical URL, publisher, publication or effective date, version, and
retrieval date. Do not cite a search snippet, an inaccessible page, or a generated
summary as though the underlying source was verified.

## 4. Build the evidence ledger while reading

Create source, evidence, and claim records as facts are inspected; never reconstruct
the ledger from memory after writing the conclusion.

- Assign one stable `S##` ID per source or version.
- Assign one `E##` row per bounded proposition or observation. Include an exact
  page, section, table, timestamp, line range, or other locator.
- Assign one atomic `C##` claim for each proposition used in the result.
- Link every evidence row to the claims it supports, contradicts, or qualifies.
- Preserve only the minimum useful excerpt; paraphrase faithfully and keep the
  locator so another reader can verify it.
- Record access limits, missing pages, extraction defects, paywalls, and suspected
  duplication as evidence-quality notes.

Use the templates in `references/investigation-format.md`. Keep the ledger in
Markdown for a compact investigation or CSV/JSON plus a Markdown source register
when it will be filtered, updated, or consumed by an app.

## 5. Form claims and citations

Write claims narrowly enough to prove or disprove. Mark each as `supported`,
`contested`, `disproven`, `unknown`, or `stale`; record confidence as `high`,
`medium`, or `low` with a short evidence-based rationale.

- Place citations immediately after the claim they support.
- Cite exact locators: page, section, table, line, timestamp, record, or revision.
- Use direct web links for web claims and stable source IDs plus pod/workspace
  locators for non-web claims.
- Label calculations, synthesis, and forecasts as analysis or inference. Cite their
  inputs and show the material assumptions.
- Do not let a citation support a broader, more certain, or more current statement
  than the source establishes.
- Avoid decorative citation piles. Prefer the few sources that directly establish
  the claim and use independent corroboration when risk warrants it.

Run two audits before publishing: trace each material sentence backward to adequate
evidence, then trace each decisive evidence item forward to the claim or conclusion
that uses it.

## 6. Resolve freshness and conflicts

Verify time-sensitive claims against a source current to the investigation's
`as_of` date. Distinguish publication date, event date, effective date, data period,
and last-updated date. Mark a source or claim stale when the required time horizon
has passed; never hide an older date behind present tense.

When sources disagree:

1. Confirm that they measure the same entity, interval, geography, unit, definition,
   and version.
2. Check whether one source cites, copies, corrects, or supersedes the other.
3. Prefer the more direct and current source only after confirming equivalent scope.
4. Seek an independent adjudicating source or underlying record.
5. Preserve the disagreement when it cannot be resolved. Mark the claim
   `contested`, explain the decision impact, and avoid averaging incompatible facts.

Keep "not found," "not accessible," "not measured," and "evidence of absence"
as different states.

## 7. Synthesize the answer

Lead with the answer, decision, or finding. Then present the decisive evidence,
important qualifications, credible counterevidence, and remaining unknowns.
Separate observed facts, inference, and recommendation. Use a comparison table or
timeline only when it exposes a real relationship.

Use the memo template in `references/investigation-format.md`. For a short response,
retain its logic in miniature: answer, evidence, conflicts, unknowns, sources, and
`as_of` date.

Stop when additional sources are unlikely to change the conclusion. Do not equate
volume with rigor.

## 8. Publish and verify the durable result

Choose the destination deliberately:

- Publish private or audience-unspecified work under
  `/me/research/<investigation-slug>/`.
- Publish collaborative work only to an authorized existing shared folder such as
  `/research/<investigation-slug>/`. Remember that every path outside `/me` is
  pod-shared; there is no `/pod` prefix.
- Treat an agent's `/me` as the invoking user's private tree, never agent-private
  storage. If a workload receives `MISSING_WORKLOAD_RESOURCE_GRANT` for a shared
  folder, report the missing grant; do not relocate or self-elevate to bypass it.
  `DELEGATION_EXCEEDS_INVOKER` is the other refusal and means the opposite: the
  agent is granted the folder and the person it acts for is not, so no grant will
  open it. Report that, and publish where the invoking user can actually write.
- Skip pod writes when the user asked only for an answer and did not authorize a
  durable artifact. Keep the result in the response and state what could be saved.

Publish at least the memo and source register for durable work; include the evidence
ledger and selected source snapshots when future verification or monitoring needs
them. Adapt filenames to an established pod workflow rather than creating a parallel
taxonomy.

```bash
research_slug="refund-policy-review"
lemma files mkdir /me/research
lemma files mkdir "/me/research/$research_slug"
lemma files upload ./memo.md "/me/research/$research_slug/memo.md"
lemma files upload ./sources.md "/me/research/$research_slug/sources.md"
lemma files upload ./evidence.csv "/me/research/$research_slug/evidence.csv" --no-search
lemma files stat "/me/research/$research_slug/memo.md"
lemma files cat "/me/research/$research_slug/memo.md"
```

Replace `/me/...` with the approved shared destination when required. After upload,
verify the exact paths, reopen the memo, check that no content was truncated, and
confirm that every cited source path or URL resolves for the intended audience.
Use `lemma files url <path>` when a signed-in pod member needs an in-app link.

Report the published path, `as_of` date, confidence, unresolved gaps, and what should
trigger a refresh.

## Related skills

- Use `lemma-user` for general pod operations and current CLI semantics.
- Use `browser` to inspect or preserve rendered web pages.
- Use `liteparse-documents` for outside-pod document parsing or OCR fallback.
- Use `lemma-data-analysis` when the main work is quantitative validation,
  computation, or charting; carry its results into this skill's evidence ledger.
- Use `lemma-artifact-author` when the final deliverable must be a polished PDF,
  DOCX, spreadsheet, or slide deck; keep this skill's source and citation contract.
