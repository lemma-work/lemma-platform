---
name: lemma-data-analysis
description: "Analyze structured data in an existing Lemma pod: inspect table schemas and RLS scope, validate data quality, define KPIs, run read-only SQL, extract records or pod files, diagnose changes, calculate with Python, create decision-ready charts or workbooks, quantify uncertainty, and publish reproducible outputs back to the pod. Use for exploratory analysis, metric design, KPI reporting, reconciliation, segmentation, funnels, cohorts, trend or anomaly diagnosis, and source-backed analytical deliverables. Do not use to redesign pod resources or build a dashboard application."
---

# Lemma Data Analysis

Turn pod data into a defensible answer and a durable, reproducible analytical
package. Treat analysis as read-only unless the user explicitly requests a data
mutation. Never change table schemas, repair source records, or publish results
outside the pod as an incidental part of analysis.

## Route the work

Read only the references needed for the task:

- Read [lemma-data-access.md](references/lemma-data-access.md) whenever the source
  is a Lemma table, record set, query, or pod file. Follow its command patterns,
  row-cap checks, file behavior, and RLS rules.
- Read [analysis-methods.md](references/analysis-methods.md) for data-quality
  assessment, KPI definition, metric diagnostics, statistical uncertainty, or
  chart selection.
- Read [reproducible-outputs.md](references/reproducible-outputs.md) before using
  Python, generating a chart or workbook, or uploading analytical outputs.

Use `lemma-user` for general pod operation, `lemma-builder` for data-model or
resource changes, `lemma-widget` for a lightweight inline live view, and
`lemma-app-design` for an interactive dashboard or application. Pair with
`lemma-artifact-author` when the final deliverable is primarily a polished
report, spreadsheet, slide deck, or PDF.

## Work from an analysis contract

Before calculating, write down:

1. State the decision or question the answer must support.
2. Define the population, unit of analysis, time field, date window, timezone,
   comparison baseline, segments, and exclusions.
3. Define every reported metric, including numerator, denominator, eligibility,
   deduplication rule, and missing-value treatment.
4. Identify the authoritative tables or files and the identity/RLS scope under
   which they are visible.
5. Set the required freshness, precision, and output form.

Resolve definitions from existing pod documentation or prior canonical reports
before asking. If multiple plausible definitions materially change the answer,
ask the user or show the branches as a sensitivity analysis; never silently pick
one. Otherwise state a reasonable assumption and test sensitivity to it.

## Follow the analytical workflow

### 1. Orient and inspect

Confirm the active pod. Inventory relevant tables and files. Read table schemas
before querying data. Establish each source's grain, primary key, join keys,
column types, enum meanings, ownership scope, and system timestamps. Do not infer
semantics from a column name when the schema or sample rows can establish them.

### 2. Acquire the smallest sufficient dataset

Push filters, joins, and aggregates into `lemma query run` when practical. Use
`lemma records export` for a reproducible row-level extract and `lemma files
download` for a tabular file stored in the pod. Preserve the raw extract
unchanged. Record the exact SQL or extraction command, snapshot time, source,
and returned row count.

### 3. Validate before interpreting

Profile completeness, uniqueness at the intended grain, valid categories,
ranges, timestamps, join coverage, duplicates, outliers, and reconciliation to
known totals. Check for silent limits and filtered/RLS-scoped populations.
Classify each finding as blocking, material but usable, or informational. Stop
and explain the evidence gap when the data cannot support the requested claim.

### 4. Calculate and diagnose

Use SQL for source-side filtering and aggregation; use the workspace Python
stack for repeatable transformations and deeper analysis. Prefer `pandas` for
tables, `numpy` for numerical work, `matplotlib` for charts, and `openpyxl` for
XLSX authoring or preservation. Keep logic in code, not manual spreadsheet
edits. Decompose metric movements before speculating about causes.

### 5. Communicate the answer

Lead with the decision-relevant result. Show the denominator and absolute count
beside rates. Distinguish observation, interpretation, and recommendation.
Label the period, timezone, units, filters, RLS scope, and freshness. Report
uncertainty and material limitations without burying the answer.

### 6. Validate and publish

Re-run the workflow from the preserved inputs, reconcile headline values to the
source queries, and visually inspect every chart. Upload the report, source
queries or script, machine-readable result data, and necessary charts/workbooks
to `/me/analysis/<slug>/` by default. Use a shared folder only when the user asks
for pod-wide access. Verify uploads with `lemma files stat` and provide a member
link only when useful.

## Apply non-negotiable quality gates

- Never describe an identity- or RLS-scoped result as pod-wide.
- Never treat a default export limit, failed join, missing row, or null as zero.
- Never mix grains without an explicit aggregation or deduplication rule.
- Never report a KPI without its definition and denominator.
- Never imply causality from observational segmentation alone.
- Never hide inconvenient data-quality findings or false precision.
- Never leave the only copy of a user-facing result in a local temp directory.
- Never upload credentials, access tokens, raw secrets, or unnecessary personal
  data with the analytical package.
