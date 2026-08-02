---
name: lemma-app-design
description: "Design, redesign, and visually refine Lemma pod apps into distinctive, production-quality interfaces grounded in the pod's real users, domain, data, files, agents, workflows, and assets. Use when defining information architecture or DESIGN.md, choosing interaction and visual direction, improving interface copy, specifying loading/empty/error/permission states, making an app responsive and accessible, critiquing screenshots, or polishing an existing HTML or Vite app. Pair with lemma-builder for LemmaClient, scaffolding, implementation, permissions, and deployment mechanics; use lemma-app-qa for exhaustive functional QA."
---

# Lemma App Design

Own the product-design layer of a Lemma app. Turn the pod's operating loop into an
interface people can understand, trust, and use repeatedly. Make the result
specific to its subject; do not ship a generic dashboard wearing the pod's name.

## Keep The Boundary Clear

- Read [`lemma-builder/references/apps.md`](../lemma-builder/references/apps.md)
  before changing an app. Treat it as authoritative for HTML versus Vite,
  `LemmaClient`, SDK hooks, auth, RLS, realtime, scaffolding, components, bundles,
  deployment, and technical testing.
- Use this skill for experience strategy, information architecture, interaction,
  visual direction, content, accessibility, responsiveness, and visual iteration.
- Use `lemma-user` to inspect a live pod, `browser` to see the rendered result,
  and `lemma-app-qa` for systematic functional and regression testing.
- Use `lemma-widget` instead when the requested surface is a compact inline
  conversation result rather than a durable app.
- Preserve the user's requested mode. For a design discussion or audit, stop at
  findings and a design specification. Implement only when the request includes
  building or changing the app.

## Follow The Design Loop

### 1. Start From Pod Truth

Inspect before proposing:

- Read the app's existing `DESIGN.md`, source, and bundle definition.
- Inspect the current rendered app and capture baseline screenshots when
  redesigning it.
- Inspect named tables and schemas, representative records, files and derived
  previews, agents, workflows and wait states, members, connectors, and existing
  domain or brand assets that the app may expose.
- Identify the primary person, their recurring job, the decision they make, the
  action they take, the cadence, and the cost of error or delay.

Use real field names, values, documents, images, vocabulary, and edge cases in the
design. If live content is unavailable, use schema-faithful representative content,
label it as sample data, and avoid fabricated business claims. Never use lorem
ipsum, arbitrary metrics, decorative charts, or controls with no real action.

### 2. Map Every Surface To The Lemma Model

Record the contract for each visible module and action:

| UI element | Human purpose | Named pod resource | `LemmaClient`/hook surface | Read/write/live behavior | Access state |
| --- | --- | --- | --- | --- | --- |

Require every prominent number, status, list, preview, agent result, form, and
button to have a truthful source or action. Respect delegated identity, RLS, and
resource grants in the experience: distinguish a genuinely empty result from
missing access, and never imply that a user's RLS-scoped rows are team-wide data.

Choose HTML or Vite with `lemma-builder` based on application complexity, not
appearance. Keep a one-page HTML app when it is sufficient; use Vite for genuine
routing, reused components, or substantial client state. Do not switch stacks just
to achieve polish. In either path, keep pod context portable through
`LemmaClient`; never design around a hard-coded pod or API host.

### 3. Define The Experience Thesis

Write one sentence:

> For **[person]**, this app turns **[input/work]** into **[decision/action]** by
> **[specific mechanism]**.

Then define:

- **Hero moment:** name the one screenshottable moment where the pod does valuable
  work, such as an agent recommendation beside its evidence and the next human
  decision.
- **First 30 seconds:** show active work, context, and a credible next action on
  the default screen; do not open with a marketing introduction.
- **Primary scenario:** trace one complete journey from arrival through a durable
  outcome in the pod.
- **Information spine:** choose the domain object or operating sequence that
  organizes the app—case, document, account, run, decision, timeline, queue, or
  another subject-specific unit.

When the user names a reference product, inspect the real experience or supplied
reference material. Extract useful interaction mechanics and hierarchy; do not
copy its surface styling or answer from a vague recollection.

### 4. Choose A Subject-Specific Direction

Derive the visual language from the subject's own world: its artifacts, materials,
instruments, notation, rhythms, constraints, and vocabulary. Privately explore two
or three credible directions, then select one with a clear rationale.

Specify:

- a 4–6 color role palette with exact values and contrast-safe pairings;
- deliberate display, body, and utility/data type roles;
- a layout principle and density appropriate to the work;
- one memorable signature element tied to the subject;
- one justified aesthetic risk, with the rest of the interface kept disciplined;
- restrained motion that clarifies change, sequence, or causality.

Apply the **subject-swap test**: if the same layout, palette, copy, and signature
could be relabeled for an unrelated pod, revise it. Avoid habitual AI-design
defaults: interchangeable KPI cards, gratuitous bento grids, gradient blobs,
glass panels, pill-shaped everything, random charts, oversized greetings, and
decoration that encodes nothing. Treat “premium” as precision, coherence, and
restraint—not as a fixed palette or luxury aesthetic.

### 5. Write The Design Specification

Create or update `DESIGN.md` before implementation. Use
[`references/design-spec.md`](references/design-spec.md). Include the evidence,
resource map, experience thesis, page map, scenarios, wireframes, tokens, copy
canon, state matrix, responsive transformations, accessibility requirements, and
acceptance criteria.

Keep routes and labels in the user's language. Expose words such as “agent,”
“workflow,” or “function” only when the intended user understands and needs those
primitives. Make one primary action obvious in each context. Keep irreversible
actions explicit and separated from routine actions. Use progressive disclosure
for secondary metadata and controls without hiding essential context.

Write interface copy as design material:

- Use plain, specific, active language and sentence case.
- Name an action by its outcome: “Approve request,” not “Submit.”
- Keep the verb stable from button to pending state, success message, and history.
- Make empty states explain what is absent and offer the next valid action.
- Make errors name what failed, preserve the user's work, and offer recovery.
- Avoid promotional filler, cute labels, vague “AI insights,” and system-centric
  wording where a human term exists.

### 6. Design Every State And Viewport

Specify loading, populated, empty, partial, error, permission, disabled, pending,
success, and destructive-confirmation states wherever they can occur. Match
skeletons to the final geometry; reserve spinners for short, local actions. Keep
agent and workflow progress legible without presenting unfinished output as final.

Design responsive transformations rather than a scaled-down desktop:

- Verify the main journey at 1440px, 1024px, 768px, and 375px where practical.
- Reorder, collapse, or move panels into drawers while preserving task priority.
- Keep the page body free of horizontal overflow at 375px.
- Keep primary actions reachable and essential context visible on touch devices.

Meet an accessible quality floor:

- Use semantic structure, programmatic labels, logical focus order, and visible
  focus treatment.
- Keep text and controls contrast-safe; never encode status by color alone.
- Keep essential actions available without hover and compact controls at least
  44px in touch target size.
- Support keyboard operation, zoom, and reduced motion.
- Associate validation messages with their fields and announce meaningful async
  updates without stealing focus.

### 7. Implement One Real Vertical Slice First

When implementation is authorized, use `lemma-builder` for the build mechanics.
Connect the primary scenario to real pod data through `LemmaClient` immediately;
do not polish a static mock and postpone the actual contract. Reuse native Lemma
blocks for proven data wiring, then restyle them through the chosen tokens instead
of forking their behavior.

Implement one complete slice—load real work, inspect it, take the primary action,
and show its result—before filling secondary pages. Keep the implementation aligned
with `DESIGN.md`; update the specification when a verified constraint changes the
design.

### 8. Review The Rendered Pixels

Use the `browser` skill for the full render-and-critique loop:

1. Open the authenticated local or deployed app and walk the primary scenario.
2. Capture screenshots at desktop and 375px, plus important intermediate states.
3. Inspect each screenshot with `view_image`; do not infer visual quality from the
   DOM, CSS, or browser snapshot alone.
4. Critique with [`references/visual-review.md`](references/visual-review.md).
5. Fix the highest-impact issue, recapture the same state, and compare.
6. Repeat until no unresolved issue compromises comprehension, task completion,
   accessibility, or the intended direction.

Inspect text wrapping, truncation, alignment, density, hierarchy, focus, contrast,
real data variation, empty/error/permission states, and breakpoint transitions.
Treat screenshots as evidence, not decoration. Never call an app polished without
viewing both a representative desktop render and the 375px experience.

## Finish With Evidence

Before handing off, confirm:

- `DESIGN.md` names real pod resources and matches the implemented experience.
- The default screen communicates the job and next action without explanation.
- The visual direction and signature belong to this subject.
- Every prominent datum and action has a real Lemma contract.
- Core loading, empty, error, permission, and pending states are designed.

When implementation is in scope, also confirm:

- Keyboard, focus, contrast, touch, reduced-motion, and 375px behavior hold.
- Desktop and mobile screenshots were visually inspected after the final change.
- The primary scenario works with real data; route deeper verification through
  `lemma-app-qa` and deployment verification through `lemma-builder`.
