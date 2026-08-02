# Investigation Format

Use these records for multi-source work. Keep IDs stable across updates so a later
research run can diff evidence and claims without breaking citations.

## Research brief

```markdown
# <Investigation title>

- Investigation ID: <slug>
- Question: <decision-relevant question>
- Audience: <person or group>
- As of: <YYYY-MM-DD, timezone if material>
- Scope: <included subjects, geography, period, comparison set>
- Out of scope: <explicit exclusions>
- Deliverable: <memo, comparison, recommendation, fact check, monitor baseline>
- Evidence threshold: <what is sufficient for the decision>
- Stop rule: <conditions for stopping or escalating>

## Subquestions

| ID | Question | Claim type | Ideal evidence | Freshness need | Status |
| --- | --- | --- | --- | --- | --- |
| Q01 | ... | factual | first-party record | current to 30 days | open |

## Assumptions and terms

- <term>: <working definition>
- <assumption>: <why it is safe enough to proceed>
```

## Source register

Assign a new source ID when the content, edition, revision, dataset period, or
snapshot changes materially. Do not overwrite the identity of an older source.

| Field | Record |
| --- | --- |
| `source_id` | Stable `S01`, `S02`, ... |
| `title` | Exact title or concise file label |
| `source_type` | pod, workspace, web, dataset, interview, or other |
| `publisher_author` | Responsible organization or author |
| `location` | Pod path, workspace path, canonical URL, or dataset identifier |
| `published_effective` | Publication, effective, revision, or data-period dates |
| `retrieved_at` | ISO date; include time and timezone for volatile material |
| `version` | Edition, commit, document revision, or snapshot marker |
| `authority` | Why this source can establish the intended claim |
| `access_notes` | Paywall, permissions, missing pages, extraction defects, or scope |
| `independence` | Original source or the upstream source it depends on |

Example:

```markdown
### S03 — Refund policy, revision 7

- Source type: pod file
- Path: /knowledge/policies/refunds.pdf
- Publisher: Operations
- Effective: 2026-07-01
- Retrieved: 2026-08-02
- Authority: Current internal policy approved for member use
- Version: revision 7
- Access notes: Pages 4-5 inspected in converted markdown and page renders
- Independence: Original policy document
```

## Evidence ledger

Create one row per bounded observation. Quote only the minimum text needed to
verify interpretation; keep longer context in notes or the source itself.

| Field | Meaning |
| --- | --- |
| `evidence_id` | Stable `E01`, `E02`, ... |
| `source_id` | Source version that contains the evidence |
| `locator` | Page, section, table, line range, timestamp, record ID, or cell range |
| `observed` | Faithful observation or short excerpt |
| `relation` | supports, contradicts, qualifies, or context |
| `claim_ids` | One or more linked `C##` claims |
| `quality` | high, medium, or low, with a concrete reason |
| `freshness` | current, dated, stale, or unknown, with relevant date |
| `notes` | Definitions, method, caveat, extraction issue, or conflict |

Example:

```markdown
| E07 | S03 | p. 4, "Eligibility" | Returns are allowed within 30 calendar days of delivery. | supports | C02 | high: governing internal policy | current: effective 2026-07-01 | Applies only to unopened items |
```

## Claim register

Keep each claim atomic. Split claims joined by "and" when their evidence or status
can differ.

| Field | Meaning |
| --- | --- |
| `claim_id` | Stable `C01`, `C02`, ... |
| `claim` | One falsifiable proposition |
| `status` | supported, contested, disproven, unknown, or stale |
| `evidence_for` | Supporting `E##` IDs |
| `evidence_against` | Contradicting `E##` IDs |
| `confidence` | high, medium, or low |
| `rationale` | Short explanation based on directness, quality, agreement, and freshness |
| `decision_role` | decisive, material, contextual, or excluded |
| `refresh_trigger` | Date, release, policy change, or new evidence that requires review |

Do not derive confidence by counting citations. Raise it when evidence is direct,
fit for the claim, independently corroborated where needed, current, and free from
unresolved material conflict.

## Citation forms

Use a source ID in every durable artifact so citations survive URL or path-display
changes. Add a direct link when one exists.

- Pod document: `[S03, p. 4]`; register source type `pod` and path
  `/knowledge/policies/refunds.pdf`.
- Pod text: `[S04, lines 22-31]`; register source type `pod` and path
  `/me/research-notes/context.md`.
- Workspace source: `[S05, src/policy.ts:88]`; record repository revision or commit.
- Web source: `[S06, "Eligibility"](https://canonical.example/policy)`.
- Dataset: `[S07, table orders, rows through 2026-07-31]`.
- Video or audio: `[S08, 12:14-13:02]`.
- Inference: `Inference from S03 and S07:` followed by assumptions or calculation.

Place the citation immediately after the smallest complete claim it supports.
Never cite the source register alone for a claim without an evidence locator.

## Conflict record

Add a conflict record whenever disagreement could change the answer:

```markdown
### X01 — <conflicted proposition>

- Sources: S02 vs S09
- Apparent disagreement: <precise difference>
- Scope check: <entity, period, geography, unit, definition, version>
- Provenance check: <independent, copied, superseded, or correction>
- Resolution: <resolved finding, or "unresolved">
- Claim status: <supported, contested, disproven, stale>
- Decision impact: <what changes under each interpretation>
- Follow-up: <best adjudicating source or event>
```

## Decision memo

```markdown
# <Answer-first title>

**As of:** <date>  
**Scope:** <one sentence>  
**Confidence:** <high, medium, or low; one-sentence basis>

## Answer

<Direct answer or recommendation. Cite material factual claims inline.>

## Decisive findings

1. <Finding and why it matters> [S01, locator]
2. <Finding and why it matters> [S02, locator]

## Counterevidence and conflicts

<Strongest credible contradiction, resolution, and decision impact.>

## Unknowns and limits

- <Unknown, why it remains unknown, and whether it could change the answer>

## Implications or next actions

- <Action tied to the evidence; label recommendations and forecasts>

## Sources

- S01 — <title, publisher, date, pod path or canonical URL>

## Method

<Scope, query classes, source selection, exclusions, and material assumptions.
Describe observable work; do not expose hidden chain-of-thought.>

## Refresh triggers

- <date, event, version, release, or new source that should reopen a claim>
```

## Suggested pod layout

Use an existing research convention when the pod has one. Otherwise use a compact
private layout and replace `/me` with an authorized shared folder only when the
audience requires collaboration:

```text
/me/research/<investigation-slug>/
  memo.md
  sources.md
  evidence.csv
  brief.md                 # keep when scope or handoff matters
  snapshots/               # keep only decisive or volatile captured sources
```

Do not duplicate every source. Preserve snapshots when the page is volatile, the
source may disappear, exact versioning matters, or a future reviewer cannot access
the original. Keep canonical URLs and retrieval dates even when a snapshot exists.

## Publication checklist

- Verify that the answer matches the stated scope and `as_of` date.
- Verify every decisive claim against an inspected source and exact locator.
- Verify calculations from their recorded inputs.
- Expose credible contradictions and unresolved unknowns.
- Distinguish current, dated, stale, and unknown evidence.
- Confirm that source IDs, paths, URLs, and cited locators resolve.
- Confirm that the destination audience is correct: `/me` for private work; an
  authorized non-`/me` folder for pod-shared work.
- Reopen the uploaded memo and confirm that content is complete and untruncated.
- Report the durable path, confidence, gaps, and refresh triggers to the user.
