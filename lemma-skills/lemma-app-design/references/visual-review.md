# Rendered App Review

Use this rubric after opening the real app with the `browser` skill. Review the
rendered pixels and the working interaction together; a clean DOM or passing build
does not establish visual quality.

## Contents

- [Prepare Comparable Evidence](#prepare-comparable-evidence)
- [Review In This Order](#review-in-this-order)
- [Record Findings Precisely](#record-findings-precisely)
- [Reject Common False Finishes](#reject-common-false-finishes)

## Prepare Comparable Evidence

1. Load representative real records, including long text, missing values, varied
   statuses, and enough items to exercise scrolling.
2. Capture the same primary scenario before and after a redesign.
3. Capture at least the default desktop view and the 375px view after the final
   change. Add 1024px and 768px when the layout changes there.
4. Capture important non-default states: selected detail, open menu/dialog, pending
   action, error, empty result, and permission state when applicable.
5. Use the same viewport, state, record, scroll position, and theme for comparisons.
6. Inspect screenshots with `view_image`. Use browser snapshots, console output,
   and network evidence for behavior—not as substitutes for viewing the image.

## Review In This Order

### 1. Five-second comprehension

- Identify the page's job, current object, state, and next action without reading
  every label.
- Reject an opening dominated by greetings, generic metrics, navigation chrome, or
  explanation instead of real work.

### 2. Task hierarchy

- Make the primary action unmistakable without making every control loud.
- Keep evidence adjacent to the decision it supports.
- Keep secondary controls available but visually subordinate.
- Preserve context across selection, agent work, workflow waits, and completion.

### 3. Subject authenticity

- Verify that the palette, type, structure, signature, and language came from the
  subject rather than a reusable AI aesthetic.
- Apply the subject-swap test. If relabeling would make this an equally plausible
  CRM, finance tool, or support app, identify what remains generic and revise it.
- Ensure the signature element carries information or improves the task; remove it
  when it is merely decoration.

### 4. Data truth

- Confirm that counts, charts, statuses, dates, agent results, and previews match
  real pod sources and scopes.
- Check realistic extremes: zero, one, many, long names, missing values, stale work,
  failure, and RLS-scoped results.
- Load more rows than one page holds. A count taken from the rows a read returned
  is not a total, and a chart grouped in the client over a capped page is a chart
  of the page. Check the figure against the source and reject any number the app
  cannot stand behind.
- Remove unsupported precision, arbitrary trends, and decorative data.

### 5. Layout and density

- Check alignment lines, panel proportions, row rhythm, whitespace, sticky regions,
  clipping, and scroll ownership.
- Keep one density per surface and one spacing logic across related elements.
- Use containers only when grouping or depth is meaningful. Remove nested cards,
  gratuitous borders, excess shadows, and radius inconsistency.

### 6. Typography and copy

- Check hierarchy, line length, wrapping, truncation, numeric alignment, and legible
  metadata at actual scale.
- Keep labels and verbs specific, stable, and in the operator's vocabulary.
- Remove filler, duplicated titles, unexplained abbreviations, vague errors, and
  empty states without a valid next step.

### 7. Color, focus, and state

- Check contrast in normal, hover, focus, selected, disabled, and error states.
- Ensure status never depends on color alone.
- Keep focus visible and coherent with the visual direction.
- Ensure loading geometry does not cause a disruptive layout jump.

### 8. Responsive behavior

- Treat 375px as its own composition, not a squeezed desktop.
- Check body overflow, panel ordering, drawers, sticky actions, touch targets,
  readable controls, keyboard overlap, and long-content behavior.
- Ensure every essential action remains reachable without hover.

### 9. Motion and feedback

- Keep motion purposeful, short, and seekable to a stable end state.
- Prevent decorative motion from competing with work or masking latency.
- Respect reduced motion and avoid moving focus unexpectedly.
- Keep pending work distinct from completed agent or workflow output.

## Record Findings Precisely

For each finding, record:

```text
Severity: blocking | high | medium | low
Viewport/state: 375px / selected case / pending approval
Evidence: screenshot path and visible symptom
Impact: what the person cannot understand or do
Fix target: the specific hierarchy, token, copy, state, or layout change
Verification: replacement screenshot and interaction result
```

Fix in this order: blocked task or inaccessible control; misleading data or state;
broken responsive layout; unclear hierarchy; inconsistent system; cosmetic detail.
Recapture the same state after each meaningful pass so the comparison is valid.

## Reject Common False Finishes

Do not approve an app merely because it has:

- a sidebar, page title, four KPI cards, and a chart;
- a fashionable palette unrelated to the subject;
- many rounded cards, gradients, glass effects, or micro-animations;
- perfect empty mock data but no real loading, error, or access state;
- a desktop screenshot while the mobile layout is unviewed;
- clean static screenshots while the primary action fails;
- a successful build or deploy without inspecting the served revision.

Approve only when the intended direction survives real content, the primary
scenario works, critical states are coherent, and final desktop and mobile pixels
have been viewed after the last change.
