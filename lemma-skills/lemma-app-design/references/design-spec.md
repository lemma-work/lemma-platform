# DESIGN.md Template

Copy this structure into the app's `DESIGN.md`. Replace every prompt with a
decision or verified fact, remove unused sections, and keep named pod resources
exact. Treat this as the experience contract; keep build commands and SDK examples
in `lemma-builder/references/apps.md`.

## Contents

- [1. Brief And Evidence](#1-brief-and-evidence)
- [2. Lemma Experience Contract](#2-lemma-experience-contract)
- [3. Experience Thesis](#3-experience-thesis)
- [4. Page And Interaction Model](#4-page-and-interaction-model)
- [5. Visual Direction](#5-visual-direction)
- [6. Content And Copy Canon](#6-content-and-copy-canon)
- [7. State Matrix](#7-state-matrix)
- [8. Accessibility And Responsive Contract](#8-accessibility-and-responsive-contract)
- [9. Acceptance Evidence](#9-acceptance-evidence)

## 1. Brief And Evidence

### Product sentence

> For **[person]**, **[app]** turns **[input/work]** into **[decision/action]** by
> **[specific mechanism]**.

### Human and job

| Item | Decision |
| --- | --- |
| Primary person | Role, skill level, and relevant access boundary |
| Recurring job | The outcome they return to complete |
| Cadence | Continuous, daily, weekly, or event-driven |
| Decision | What they must understand or choose |
| Durable action | What changes in the pod when they finish |
| Stakes | Cost of error, ambiguity, or delay |

### Evidence inventory

List only evidence actually inspected:

- Existing app and screenshots:
- Tables, schemas, and representative record IDs:
- Files, previews, and domain assets:
- Agents and representative final outputs:
- Workflows, waits, and form schemas:
- Members, connectors, or permission constraints:
- Reference products or supplied visual references:
- User constraints and explicit preferences:

Record unknowns separately. Do not quietly turn an assumption into product truth.

## 2. Lemma Experience Contract

### App path

- Path: **HTML / Vite**
- Reason: [complexity-based reason]
- Existing path preserved: **yes / no**, with rationale if changed

### Surface map

| Surface or control | Human purpose | Named resource | Field/content source | `LemmaClient` or hook | Behavior | Access/RLS implication |
| --- | --- | --- | --- | --- | --- | --- |
| Example: waiting queue | Choose the next case | `cases` table | `title`, `priority`, `status` | `useLiveRecords` | Read + live | Current user's rows only |

Reject any prominent UI element that cannot fill this table truthfully. Name
aggregate definitions, sort rules, freshness behavior, and final agent-output
selection where relevant.

## 3. Experience Thesis

### Hero moment

Describe one frame that proves the pod's value. Include the source evidence, the
human decision, and the next action visible in that frame.

### First 30 seconds

Describe exactly what appears on first load with representative real content.
Prefer active work and the next action over a welcome screen or generic overview.

### Primary scenario

Write one concrete end-to-end sequence:

1. The person arrives from [entry point].
2. They recognize [work/context] because [evidence].
3. They inspect or change [resource].
4. The app starts/resumes/writes [named operation].
5. They see [pending and completion feedback].
6. The durable result is visible in [named resource/history].

Add secondary scenarios only when they materially change the layout or state model.

### Information spine

Name the object or sequence that organizes the product. Explain why it matches the
person's mental model better than a generic dashboard hierarchy.

## 4. Page And Interaction Model

### Page map

| Route/view | Single job | Primary content | Primary action | Secondary action | Entry/exit |
| --- | --- | --- | --- | --- | --- |

### Interaction rules

Define:

- selection and navigation behavior;
- filters, search, sort, and persistence;
- optimistic, pending, completed, and failed action feedback;
- agent streaming versus final-output treatment;
- workflow wait, assignment, and resume behavior;
- destructive confirmation and recovery;
- realtime change behavior without polling;
- keyboard behavior and focus movement.

### Wireframes

Draw compact ASCII wireframes for desktop and 375px. Label regions by purpose,
not implementation.

```text
DESKTOP
┌──────────── work queue ───────────┬──────── selected case ────────┐
│ filters                           │ evidence                      │
│ ranked items                      │ recommendation                │
│                                   │ [primary decision]            │
└───────────────────────────────────┴───────────────────────────────┘

375 PX
┌──────────── active work ──────────┐
│ filters [drawer]                  │
│ ranked items                      │
└───────────────────────────────────┘
selected case → full-screen detail
```

Explain what reorders, collapses, becomes a drawer, becomes full-screen, or stays
sticky at each breakpoint. Never say only “stack on mobile.”

## 5. Visual Direction

### Subject cues

List the domain's real artifacts, materials, notation, rhythms, and vocabulary.
State which cues enter the design and which would become costume or cliché.

### Direction statement

Describe the chosen tone in one sentence. Include why it helps this person do this
job. Record rejected directions and the reason for rejecting them when the choice
was consequential.

### Signature

Name one memorable, useful element tied to the subject. State what it encodes and
why it would not belong unchanged in an unrelated app.

### Tokens

```text
COLOR
canvas      #......
surface     #......
text        #......
muted       #......
accent      #......
critical    #......

TYPE
display     [family / fallback / size / weight / line-height]
body        [family / fallback / size / weight / line-height]
utility     [family / fallback / tabular behavior]

SPACE       4 / 8 / 12 / 16 / 24 / 32
RADIUS      [limited roles]
BORDER      [subtle / strong]
SHADOW      [only where depth is meaningful]
MOTION      [purpose / duration / easing / reduced-motion result]
```

Check text, control, status, focus, and selected-state contrast before approving
the palette. Use status labels or symbols in addition to color.

## 6. Content And Copy Canon

### Vocabulary

| Concept | Use | Do not use | Reason |
| --- | --- | --- | --- |

### Action language

| Intent | Button | Pending | Success | Error/recovery |
| --- | --- | --- | --- | --- |

Keep one verb through the sequence. Write from the operator's side of the screen;
avoid exposing implementation primitives unless the operator needs them.

### Representative content

Include realistic titles, names, timestamps, statuses, long values, missing values,
and edge cases from inspected pod data. Mark schema-faithful sample content clearly
when live data is unavailable.

## 7. State Matrix

| Surface | Loading | Populated | Empty | Partial/stale | Error/recovery | Permission | Pending/success |
| --- | --- | --- | --- | --- | --- | --- | --- |

Distinguish “no matching rows,” “no rows created,” “not allowed to see rows,” and
“more rows matched than this view returned.” The last one is the partial column's
job: name what the surface shows when a page cap or a `truncated` query result
means the visible set is a prefix, and what the person does to see the rest.
Preserve inputs across recoverable errors. Specify skeleton geometry, progress
language, retry behavior, and focus destination.

## 8. Accessibility And Responsive Contract

Record explicit requirements for:

- headings and landmarks;
- labels, descriptions, and validation associations;
- focus order, focus return, and keyboard shortcuts;
- contrast and non-color status encoding;
- 44px touch targets and hover-independent actions;
- reduced motion and zoom;
- announcements for important async changes;
- 1440px, 1024px, 768px, and 375px transformations;
- truncation, wrapping, and overflow behavior with real long content.

## 9. Acceptance Evidence

Define observable gates:

- [ ] The primary scenario completes against real pod data.
- [ ] Every prominent datum and control maps to a named resource and client surface.
- [ ] The hero moment is visible without a narrated explanation.
- [ ] The subject-swap test fails: the identity cannot move unchanged to an unrelated app.
- [ ] Loading, empty, error, permission, pending, and success states are intentional.
- [ ] Keyboard, focus, contrast, touch, reduced-motion, and zoom behavior hold.
- [ ] The page has no body-level horizontal overflow at 375px.
- [ ] Final desktop and 375px screenshots were inspected with `view_image`.
- [ ] Visual-review findings and their resolutions are recorded.
