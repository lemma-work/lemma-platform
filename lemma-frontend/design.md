# Lemma Frontend Design Digest

This digest documents the current product UI in this repository. It is based on the implemented Next.js app, Tailwind v4 token setup, shadcn/Radix primitives, pod workspace screens, records/tables, flows, assistants, desk runtime, dashboard, and landing surfaces.

## 1. Visual Theme & Atmosphere

Lemma currently reads as a warm operational workspace: calm, dense where work is dense, and intentionally low-drama. The app canvas is warm parchment (`--bg-canvas: #f5f4f0`), while primary work surfaces stay white (`--bg-surface` / `--surface-1: #ffffff`). The core visual language is "border-first": most surfaces are separated by warm grey borders and small ring shadows instead of heavy elevation.

Brand primary is indigo (`#6366f1` in light mode), the CTA matches (`#6366f1`), and the active accent is gold (`#d99a32`). Coral (`#df6a45`) appears as the attention/semantic signal.


**Key Characteristics**

- Warm parchment app canvas: `#f5f4f0`, not stark white.
- Indigo primary identity: `#6366f1` light mode, `#818cf8` dark mode.
- Gold accent: `#d99a32` for delight, progress, and active rails.
- Coral attention: `#df6a45` for human review and needs-response signals.
- Border-first containment with subtle ring shadows.
- Operational density in records/tables: sticky table headers, inline editable cells, side sheets, compact metadata.
- Compact radius in primitives: `4px`, `6px`, `8px`, `10px`, `12px`, `9999px`.
- Larger radius only in softer home/landing/template surfaces.
- Motion is restrained: fade, slide-up, breathing loader, ambient drift only where it serves atmosphere.

## 2. Color Palette & Roles

### Light Mode Core

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Canvas | `--bg-canvas` | `#f5f4f0` | App background, page base |
| Surface | `--bg-surface`, `--surface-1` | `#ffffff` | Cards, sheets, tables, popovers |
| Subtle Surface | `--bg-subtle`, `--surface-2` | `#f0efec` | Table hovers, inputs, secondary panels |
| Muted Surface | `--bg-muted`, `--surface-3` | `#e8e6e2` | Secondary buttons, selected rails |
| Brand Primary | `--brand-primary` | `#6366f1` | Logo bars, selected states, strong identity |
| Brand Secondary | `--brand-secondary` | `#6b6a66` | Supporting brand text and chart color |
| Brand Accent | `--brand-accent` | `#d99a32` | Delight, progress, active rails, highlights |
| Brand Warm | `--brand-warm` | `#d99a32` | Atmospheric warmth (aliases accent) |
| Brand Coral | `--brand-coral` | `#df6a45` | Attention, human-review emphasis |
| Brand Tan | `--brand-tan` | `#e7dbc9` | Accent button and brand badge fill |
| Focus Blue | `--focus-blue` | `#6366f1` | Accessible focus rings |
| CTA Background | `--cta-bg` | `#6366f1` | Primary buttons |
| CTA Foreground | `--cta-fg` | `#ffffff` | Primary button text |

### Text Scale

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Primary | `--text-primary` | `#141414` | Headings, active nav, important cells |
| Secondary | `--text-secondary` | `#6b6b6b` | Body text, table content, nav labels |
| Tertiary | `--text-tertiary` | `#9a9a9a` | Metadata, labels, helper copy |
| Soft | `--text-soft` | `#b0b0b0` | Placeholders, muted unavailable values |
| Inverse | `--text-inverse` | `#ffffff` | Text on dark fills |
| On Brand | `--text-on-brand` | `#ffffff` | Text on brand/CTA fills |

### Borders

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Subtle | `--border-subtle` | `#e5e5e5` | Default separators and quiet card borders |
| Default | `--border-default` | `#d4d4d4` | Inputs, active card boundaries |
| Strong | `--border-strong` | `#c0c0c0` | Hovered controls and emphasized boundaries |

### States

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Success | `--state-success` | `#16a34a` | Ready state, positive badges, booleans |
| Warning | `--state-warning` | `#d97706` | Unsaved, pending, date/time type badges |
| Error | `--state-error` | `#dc2626` | Destructive actions, errors, delete affordances |
| Info | `--state-info` | `#0891b2` | Info badges, focus support, flow/data marks |

### Dark Mode Core

Dark mode preserves the same structure with deep neutral surfaces:

- `--bg-canvas: #11120f`
- `--bg-surface: #1a1a1a`
- `--bg-subtle: #222222`
- `--bg-muted: #2a2a2a`
- `--text-primary: #ececec`
- `--text-secondary: #9e9e9e`
- `--text-tertiary: #6e6e6e`
- `--border-subtle: #2a2a2a`
- `--border-default: #3a3a3a`
- `--border-strong: #555555`
- `--cta-bg: #818cf8`
- `--cta-fg: #ffffff`

## 3. Typography Rules

### Implemented Font Families

- Product UI: `IBM Plex Sans`, via `--font-ibm-plex-sans`.
- Code/technical UI: `Source Code Pro`, via `--font-source-code-pro`.
- Landing serif: `Fraunces`, via `--font-landing-serif`.
- Landing sans: `Inter`, via `--font-landing-sans`.
- Landing mono: `IBM Plex Mono`, via `--font-landing-mono`.


### Product Type Scale

| Token | Size | Use |
| --- | --- | --- |
| `--text-xs` | 12px | Labels, metadata, chips |
| `--text-sm` | 14px | Dense UI, sidebar, table labels |
| `--text-base` | 16px | Body baseline |
| `--text-md` | 18px | Secondary headings |
| `--text-lg` | 20px | Card/dialog titles |
| `--text-xl` | 24px | Page headings |
| `--text-2xl` | 30px | Large page title |
| `--text-3xl` | 36px | Create/onboarding headings |
| `--text-4xl` | 44px | Marketing/product hero support |
| `--text-5xl` | 52px | Large display |
| `--text-6xl` | 64px | Max display |

### Type Roles — pick by role, never by size

The scale above is the *implementation*. Product UI selects from these roles,
which each have exactly one correct answer. Reaching past them for a literal
`font-size` is what produced 53 distinct sizes across the feature stylesheets,
33 of them inside a single 8px band.

| Role | Class | Size | Weight | Tracking | Use |
| --- | --- | --- | --- | --- | --- |
| Eyebrow | `.type-eyebrow` | 12px | 600 | `wider` | Uppercase section labels, kickers |
| Eyebrow (dense) | `.type-eyebrow-sm` | 11px | 600 | `wider` | Pills, column headers, table meta |
| Eyebrow (mono) | `.type-eyebrow-mono` | 12px | 500 | `widest` | Technical/status eyebrows |
| Meta | `.type-meta` | 12px | 400 | `normal` | Counts, timestamps, helper copy |
| Body | `.type-body` | 14px | 400 | `normal` | Default UI text, descriptions |
| Body strong | `.type-body-strong` | 14px | 600 | `normal` | Row titles, emphasized body |
| Title | `.type-title` | 18px | 600 | `snug` | Card, panel, and dialog titles |
| Page title | `.type-page-title` | 24px | 700 | `tight` | Page headings |
| Display | `.type-display` | 36px | 700 | `tight` | Onboarding and hero headings |

**Rule.** If no role fits, add a role here — do not write a literal `font-size`
in a feature stylesheet. `styles/features/**` is audited
(`rawFontSizeDeclaration`) and any increase fails CI.

`.type-micro-label` and `.type-eyebrow-medium` are deprecated: they differ from
`.type-eyebrow` only in weight. They still render as they always have, and
migrate to `.type-eyebrow` during the Phase 3 cleanup with visual QA.

### Density

Spacing comes from `--space-*`; `padding` and `gap` in feature CSS had 0% token
adoption, which is the main reason comparable surfaces sit at visibly different
densities. Three densities, chosen by surface — not per element:

| Density | Row padding | Gap | Use |
| --- | --- | --- | --- |
| Compact | `--space-2` (8px) | `--space-2` | Tables, ledgers, dense lists, side rails |
| Standard | `--space-3` (12px) | `--space-3` | Cards, panels, forms, most chrome |
| Roomy | `--space-5` (20px) | `--space-4` | Empty states, onboarding, hero surfaces |

Pick one density for a surface and hold it. Mixing densities inside a single
panel is the specific defect that reads as "hand-folded".

### Leading & Tracking

- Tight: `--leading-tight: 1.2`
- Snug: `--leading-snug: 1.35`
- Normal: `--leading-normal: 1.5`
- Relaxed: `--leading-relaxed: 1.7`
- Tight tracking: `--tracking-tight: -0.02em`
- Snug tracking: `--tracking-snug: -0.01em`
- Normal tracking: `0`
- Label tracking: `0.04em`, `0.08em`, `0.16em`

### Typographic Behavior

- Product `h1` and `h2` use IBM Plex Sans, `font-weight: 700`, and `letter-spacing: -0.03em`.
- Product `h3` to `h6` use IBM Plex Sans, `font-weight: 700`, and `letter-spacing: -0.01em`.
- UI components often use `text-sm`, `font-medium` or `font-semibold`.
- Labels use uppercase at 10px to 12px with `0.08em` to `0.16em` tracking.
- Table cells use small type, truncation, and tabular numbers for numeric fields.
- Landing pages intentionally diverge: Fraunces light/italic display, Inter body, larger visual rhythm.

## 4. Spacing, Radius & Layout

### Spacing Scale

The global scale is tokenized. Micro and half steps exist because dense chrome
genuinely lands there — without them, 71 distinct one-off padding values had
accumulated in the feature stylesheets:

- `--space-px`: 1px — hairlines, mark insets (never layout)
- `--space-0-5`: 2px — mark and loader internals
- `--space-1-5`: 6px — icon-to-label, compact rows
- `--space-2-5`: 10px — pill interiors
- `--space-3-5`: 14px — compact panel padding

Integer steps:

- `--space-1`: 4px
- `--space-2`: 8px
- `--space-3`: 12px
- `--space-4`: 16px
- `--space-5`: 20px
- `--space-6`: 24px
- `--space-8`: 32px
- `--space-10`: 40px
- `--space-12`: 48px
- `--space-16`: 64px
- `--space-20`: 80px
- `--space-24`: 96px
- `--space-32`: 128px

### Radius Scale

Declared once, in the `@theme` block of `styles/tokens.css`. Tailwind's
`rounded-*` utilities and `var(--radius-*)` both resolve from there, so a
utility and a token can never disagree. Radius is theme-independent — never
redeclare it in `:root` or a `.dark` scope.

| Token | Value | Use |
| --- | --- | --- |
| `--radius-xs` | 2px | Brand mark bars, loader bars |
| `--radius-sm` | 4px | Checkboxes, tiny pills |
| `--radius-md` | 6px | Buttons, inputs, tabs, nav items |
| `--radius-lg` | 8px | Dialogs, popovers, table containers, cards |
| `--radius-xl` | 10px | Cards, panels, table shells |
| `--radius-2xl` | 12px | Assistant shells, larger panels |
| `--radius-3xl` | 16px | Soft cards — templates, assistant approval cards |
| `--radius-4xl` | 24px | Softest surfaces — office/stage chrome |
| `--radius-full` | 9999px | Pills, avatars, circular buttons |

Actual components also use Tailwind utility radii:

- `rounded-md`: default buttons, inputs, tabs, nav items.
- `rounded-lg`: dialogs, popovers, table containers, cards.
- `rounded-xl`: table shells, builder rows, kanban columns.
- `rounded-2xl` / `rounded-[1.4rem]` / `rounded-[1.6rem]`: home, template, and softer dashboard cards.
- `rounded-full`: pills, profile buttons, selected floating action bar.

## 5. Depth & Elevation

Lemma uses a ring-first elevation model. Most surfaces are flat or nearly flat; high elevation is reserved for overlays, drawers, popovers, and selected-row action bars.

| Level | Token | Treatment | Use |
| --- | --- | --- | --- |
| Flat | none | Border only | Static cards, table rows, sidebars |
| Ring | `--shadow-xs` | `0 0 0 1px` subtle text mix | Table shells, active tabs, small icon blocks |
| Small | `--shadow-sm` | Ring plus 1-2px shadow | Cards, assistant strips, hover surfaces |
| Medium | `--shadow-md` | 10px blur, clipped | Interactive card hover, landing CTA |
| Large | `--shadow-lg` | 25px blur | Dialogs, popovers, sheets, selected-row bar |
| Extra Large | `--shadow-xl` | 32px blur | Highest priority overlays only |

Rule: if a surface is not floating, interactive, or selected, prefer border and background contrast before adding shadow.

## 6. Responsive & Mobile

The product must be usable on 375–430px phones. The shell adapts; complex builders degrade gracefully rather than pretending to work.

**Breakpoints.** Tailwind defaults: `sm` 640, `md` 768, `lg` 1024, `xl` 1280. `md` is the shell boundary: below it, inline sidebars are hidden and navigation moves into off-canvas drawers (`MobileSidebarDrawer` in the pod shell, the Sheet-based drawer in home/dashboard chrome). Feature CSS uses max-width 980/860/640 queries; new sections need a usable state at 375px.

**Rules for new UI**

- Never gate an action on hover alone. The global override in `styles/utilities.css` keeps `opacity-0 group-hover:opacity-100` reveals visible on touch; display/visibility-based reveals are flagged by the design audit (`hoverOnlyDisplayReveal`).
- Every `height: 100vh` needs a `100dvh` fallback on the next declaration (audit: `viewportHeightWithoutDvhFallback`). Tailwind `*-screen` utilities are already remapped to `dvh` globally.
- No unconditional fixed widths ≥360px; clamp with `max-w-*`, `min()`, or a breakpoint prefix (audit: `fixedWidthsWiderThanPhones`). Dialogs inherit `w-[calc(100vw-2rem)]` from `DialogContent` — don't override `w-` back to a fixed pixel value.
- Touch targets: shared `Button`, `Checkbox`, and `.lemma-shell-icon-button` get an invisible 44px hit-slop on coarse pointers via `.tap-target`. Add `tap-target` to new compact custom controls.
- Form controls must render at ≥16px on touch (global rule in `styles/base.css`) or iOS zooms on focus; don't undo it with smaller arbitrary font sizes on inputs.
- Tooltips never fire on touch — icon-only actions need an `aria-label` and should not hide essential information behind a tooltip.
- Pinch zoom stays enabled (`viewport` in `app/layout.tsx`); never reintroduce `maximumScale: 1` / `userScalable: false`.
- Canvas/editor surfaces (flows, Monaco) are view-only below `md`: hide palettes and config asides, disable drag/connect, show a "open on a larger screen to edit" notice.
- QA every new surface at 375px before shipping: no horizontal scroll on the page body, reachable primary actions, readable text.

## 7. Identity & Wayfinding in the Pod Shell

The pod shell can print a resource's name in four places at once: the workspace
tab strip, the sidebar, the context bar, and the page body. Left alone they all
do, and an agent called "Pitch Polish" appears three or four times inside a
couple of hundred pixels. Deleting a band is the wrong fix — the context bar
also carries the back link and the actions, so removing it to stop the
repetition costs real affordances.

### The bands

Each band answers a different question, and is set at a different altitude so
the appearances that remain read as different kinds of statement rather than as
repeated headings.

| Band | Question it answers | Altitude | Can it cede? |
| --- | --- | --- | --- |
| Workspace tab strip | "What do I have open?" | 14px / 400 | No — a tab without a label is useless |
| Sidebar row | "Where does this live?" | 12px / 400 | No — nav must mark its active row |
| Context bar | "What am I acting on?" | 16px / 600 | **Yes** |
| Page body | "What is it made of?" | — | Must never re-print the name |

The context bar is the only band that can yield, because the other two are
structurally required and the page body should never have printed the name in
the first place.

### Who owns the title

`titleOwner` on `ResourceHeader` decides, and the rules are pinned by tests in
`lib/pods/topbar-title.test.ts`:

- **`'bar'` (default)** — the bar prints the name. Correct for index routes and
  anything without a tab of its own.
- **`'page'`** — the route renders its own hero heading, so the bar cedes for
  as long as that heading is on screen, and takes it back on scroll.
- **`'tab'`** — the workspace tab strip is already printing this name directly
  above the bar, so the bar drops to the back link, mode switch, and actions.
  If the strip stops showing it, the bar takes the title back, so the resource
  is never left unnamed.

Ceding only fades the title text. The back link, mode switch, `meta`, and
actions are outside that toggle and stay put — which is the whole reason to
cede ownership rather than delete the header.

### Rules for new routes

- A detail route that gets a workspace tab should use `titleOwner="tab"`.
- Never render the display name as a heading in the page body. If the canonical
  id is genuinely useful, label it (`IDENTIFIER`) and set it at `.type-meta` or
  mono so it reads as a field, not a title.
- Never solve duplication by deleting `ResourceHeader`. That loses the back
  target and the action anchor, and the shell then has nothing to render into
  its context bar. Hand the title to another band instead.

## 8. Buttons & Action Hierarchy

### Variants — pick by role, never by appearance

Variants name what an action **is**, not what it looks like. Naming them after
appearance is what let `outline` and `secondary` drift into being the same
button under two names, and made every call site a fresh aesthetic judgement.

| Variant | Role | Use for |
| --- | --- | --- |
| `primary` | The action this view exists to perform | Dialog confirm, form submit, empty-state create |
| `secondary` | A real alternative, or a create action where content is the focus | Page-header "New X", retry, alternate path |
| `quiet` | Present but not competing | Row actions, dismissals, "Done", navigate-away |
| `destructive` | Removes or revokes | Delete, disconnect, revoke |
| `link` | An action inside a sentence | Inline text actions |

`secondary` is the component default. Nothing should read as the call to action
without someone having said so — a default of `primary` produced 108 accidental
CTAs against 3 deliberate ones.

### One primary per view

A view is whatever the user is looking at as one thing: a page, a dialog, a
wizard step. **At most one `primary` button may be reachable in a view at once.**

Mutually exclusive branches don't count against each other — a header CTA and an
empty-state CTA never render together, and neither do two steps of a wizard.

`npm run design:audit` counts these. Two primaries live in the same view is a
finding, not a style preference: when everything is emphasised, nothing is.

### A page header's create button is not primary

The content is what the user came for. A solid CTA in the header competes with
it for attention and wins, which is backwards. Header creates are `secondary`;
the empty state — where creating really is the only thing to do — gets `primary`.

### Rows don't carry buttons

In a list, make the row itself the target. A button on every row is one CTA per
record, which is the same failure as a page with six primaries. Reserve buttons
for the one action per section, and let row-level affordances be `quiet` or a
chevron.
