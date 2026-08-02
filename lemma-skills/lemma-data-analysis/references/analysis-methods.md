# Analysis Methods

Match rigor to the decision risk. Prefer a smaller, well-defined answer over a
larger analysis built on ambiguous grain or weak evidence.

## Validate data quality

Check the dimensions that can change the conclusion:

- **Scope:** intended population, time window, timezone, RLS visibility, filters,
  row caps, and source freshness.
- **Grain:** uniqueness at the intended unit, duplicated events, and many-to-many
  joins that multiply rows.
- **Completeness:** nulls, empty strings, missing dates, missing categories, and
  coverage by period or segment. Do not convert missing to zero without a rule.
- **Validity:** enum membership, type/parse failures, impossible ranges, negative
  values, and start/end ordering.
- **Consistency:** totals across sources, status/category mappings, units,
  currencies, and timezone conversions.
- **Relationships:** unmatched keys, orphan rows, join loss, and changing keys.
- **Stability:** outliers, backfills, late arrivals, seasonality, and definition
  changes that create artificial breaks.

Quantify each issue: affected rows, share of the relevant denominator, segments
or dates affected, likely direction of bias, and whether it blocks the decision.
Preserve both pre-cleaning and post-cleaning counts. Never silently discard data.

## Define KPIs precisely

For each KPI, record:

- name, decision served, owner, and definition version;
- unit of analysis and eligibility population;
- numerator, denominator, aggregation, and deduplication key;
- event timestamp, reporting window, timezone, and late-arrival rule;
- null, cancellation, refund, test-data, and outlier treatment;
- source tables/fields, required joins, refresh cadence, and RLS scope;
- target, comparison baseline, segmentation dimensions, and guardrails.

Report counts with rates. Avoid averaging pre-aggregated averages or combining
incompatible denominators. Separate leading indicators, outcome metrics, and
guardrails. When two plausible definitions produce different decisions, show
both and recommend a canonical definition.

## Diagnose metric movement

Verify the movement before explaining it:

1. Recompute both periods with the same definition and complete windows.
2. Check freshness, backfills, instrumentation, population, and denominator
   changes.
3. Decompose into volume, rate, and mix effects.
4. Slice by time, segment, lifecycle/cohort, channel, geography, product, or
   workflow stage only where sample size and business meaning support it.
5. Rank segment contributions to the total change, not only relative growth.
6. Test plausible explanations against evidence and actively seek disconfirming
   evidence.
7. Separate confirmed drivers, contributing signals, and unresolved hypotheses.

Define the decomposition method, including allocation of interaction terms, and
verify that contributions reconcile to the total change. Analyze overlapping
dimensions such as device, channel, and geography separately; never sum their
contributions as though they were mutually exclusive. Select a primary exclusive
segmentation hierarchy for attribution, or use an explicit multivariate method
and disclose its assumptions.

Do not call a correlated segment causal. A causal claim needs an experiment,
credible quasi-experiment, or an explicit causal design and assumptions.

## Represent uncertainty honestly

- Show numerator, denominator, sample/population coverage, and data-quality
  uncertainty before adding statistical machinery.
- Report confidence intervals or sensitivity ranges when sampling, estimation,
  sparse segments, or model assumptions matter.
- Pair statistical significance with effect size and practical relevance; do not
  use a p-value as the conclusion.
- Distinguish sampling uncertainty from measurement error, missing data,
  definition ambiguity, and selection bias.
- Use sensitivity analysis for alternate windows, deduplication rules, outlier
  treatment, and plausible missing-data assumptions.
- Round to the precision supported by the data and label estimates as estimates.

## Choose the smallest useful visual

| Question | Prefer | Avoid |
| --- | --- | --- |
| Change over time | Line or small multiples | Dense labels and smoothed lines that hide raw movement |
| Rank categories | Sorted horizontal bars | Unsorted bars or 3D charts |
| Compare composition | Stacked bars; 100% stacked for shares | Pie/donut charts with many slices |
| Inspect distribution | Histogram, box plot, or ECDF | Mean-only summaries |
| Examine relationship | Scatter with transparency or faceting | Dual axes that imply a relationship |
| Compare cohorts | Cohort table or heatmap | Overplotted spaghetti lines |
| Show exact values | Compact table | A chart that makes lookup harder |

Write an answer-first title. Label units, time window, timezone, source, scope,
and denominator. Use zero baselines for bars, consistent scales for comparisons,
direct labels where possible, and accessible colors. Annotate material events,
not every point. Show missing periods as missing rather than interpolating them.
Check every plotted value against the calculation table.
