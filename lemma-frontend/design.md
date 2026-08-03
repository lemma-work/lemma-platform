# Lemma Frontend Design Digest

This digest documents the current product UI in this repository. It is based on the implemented Next.js app, Tailwind v4 token setup, shadcn/Radix primitives, pod workspace screens, records/tables, flows, assistants, desk runtime, dashboard, and landing surfaces.

## 1. Visual Theme & Atmosphere

**Ink on paper.** The product speaks the landing page's language: near-black ink (`#17181a`) on warm paper (`--bg-canvas: #f2efe7`), alpha hairlines instead of grey borders, and four saturated jewel tones carrying meaning. Editorial, not chrome — the palette is supposed to have an opinion.

This is a deliberate reversal of an earlier pass that matched the macOS system appearance. That version was native and correct and completely inert: Apple's system palette is designed to be a neutral *host* for someone else's content, so adopting it literally produces a product with no point of view. What survived from it is the part that was actually about behaviour — appearance-following, materials, control geometry, alpha-based rules.

**One voice across the signup boundary.** The app and the page that sells it are now set in the same typeface, use the same badge tones, and share a button radius. `landing-type.css` had already argued this out internally ("one family, one scale"); the same argument applies across the product line.

**Key Characteristics**

- Warm paper canvas: `#f2efe7` light, `#131311` dark. Never a neutral grey — every neutral carries warmth.
- Near-black ink, never pure black: `#17181a`.
- Violet `#5a3fd4` carries action. Green confirms, amber delights, rust asks.
- Alpha hairline **rules**, not borders — they hold on white, on cream, and on a tinted row alike.
- Inter everywhere, DM Mono for machine values. Hierarchy from weight and tracking, never a second face.
- Real negative tracking: `-0.032em` display, `-0.016em` titles.
- Elevation is paper, not glass — shadows cast in ink, sheets close to the page.
- Radius: `8px` controls (the landing button), `14px` cards (the landing header pill).
- Motion is restrained: fade, slide-up, breathing loader, ambient drift only where it serves atmosphere.

### The badge pairs

Each semantic role is a **landing badge pair** — a saturated foreground and the soft fill it was drawn to sit on. A status chip in the app and a badge on the marketing page are the same object, not two takes on the same idea.

| Role | Token | Foreground | Soft fill |
| --- | --- | --- | --- |
| Action / collaboration | `--action-primary`, `--collaboration` | `#5a3fd4` | `#e6e0ff` |
| Success | `--state-success` | `#11743c` | `#d9f5e3` |
| Delight / warning | `--delight`, `--state-warning` | `#8a6400` | `#fff3c4` |
| Attention / error | `--attention`, `--state-error` | `#c22f15` | `#ffe1da` |
| Intelligence | `--intelligence` | `#d97757` | `#f2ebe6` |

Terracotta is the assistant's colour on the landing and stays the assistant's colour here.

Every foreground was picked by the landing to be legible as text on its own fill, which is why they can serve both roles without an accessible-variant split.

### Deliberate divergences

Three families of colour are correctly *not* on this palette, and changing them is a bug:

- **Third-party brand assets.** `.connector-logo-tile`, `.surface-logo-chip`, and the WhatsApp/Telegram/Slack/Teams starter previews carry their owners' colours. They are chipped on white in both appearances because a black GitHub mark vanishes on a dark surface.
- **Art-directed pages.** `github-import-page.css` and `remix-page.css` share a self-contained `--import-*` stationery palette and override the token set on purpose.
- **Landing.** `styles/features/landing-*.css` is its own visual world.

## 2. Color Palette & Roles

### The accent channel

`--accent-rgb` is a **channel triple** (`90 63 212`), not a colour. That is what
lets tints be written as a plain alpha — `rgb(var(--accent-rgb) / 0.12)` — while
still working inside `color-mix()`. Read it; never hardcode `#5a3fd4`.

| Token | Resolves to | Use |
| --- | --- | --- |
| `--accent-rgb` | `90 63 212` light, `139 122 245` dark | The channel itself |
| `--accent-fg-rgb` | `255 255 255` | Legible foreground on an accent fill |
| `--action-primary` | `rgb(var(--accent-rgb))` | Every CTA, selected state, focus ring |
| `--action-primary-hover` | accent × 84% black (light), × 84% white (dark) | Pressed/hover fill |
| `--action-primary-soft` | `#e6e0ff` | Accent-tinted backgrounds |

The desktop shell can overwrite this channel with the user's macOS System
Settings accent (`NSColor.controlAccentColor`, read before first paint and
re-read on focus). It is **off by default** behind `LEMMA_DESKTOP_SYSTEM_ACCENT=1`:
handing the product's one loud colour to a system preference is a plausible
*setting*, not a plausible identity.

### Light Mode Core

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Canvas | `--bg-canvas` | `#f2efe7` | Warm paper — deeper than the sheets on it |
| Surface | `--bg-surface`, `--surface-1` | `#ffffff` | Cards, sheets, tables, popovers |
| Subtle Surface | `--bg-subtle`, `--surface-2` | `#f7f5ef` | Table hovers, inputs, secondary panels |
| Muted Surface | `--bg-muted`, `--surface-3` | `#edeae1` | Secondary buttons, selected rails |
| Brand Primary | `--brand-primary` | `#5a3fd4` | Selected states, strong identity |
| Brand Secondary | `--brand-secondary` | `#62666b` | Supporting text, chart colour |
| Brand Accent | `--brand-accent` | `#8a6400` | Delight, progress, active rails |
| Brand Warm | `--brand-warm` | `#c0801f` | Atmospheric warmth |
| Brand Coral | `--brand-coral` | `#c22f15` | Attention, human-review emphasis |
| Brand Sky | `--brand-sky` | `#d97757` | Terracotta — the assistant's colour |
| Brand Tan | `--brand-tan` | `#f0ece1` | Badge fill |
| CTA Background | `--cta-bg` | `rgb(var(--accent-rgb))` | Primary buttons |
| CTA Foreground | `--cta-fg` | `rgb(var(--accent-fg-rgb))` | Primary button text |

### Text Scale

Ink, not grey. `#17181a` is the landing's ink and reads as printed rather than
as a screen default — pure black is never used.

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Primary | `--text-primary` | `#17181a` | Headings, active nav, important cells |
| Secondary | `--text-secondary` | `#62666b` | Body text, table content, nav labels |
| Tertiary | `--text-tertiary` | `#888b90` | Metadata, labels, helper copy |
| Soft | `--text-soft` | `#a8abaf` | Placeholders, muted unavailable values |
| Inverse | `--text-inverse` | `#ffffff` | Text on dark fills |
| On Brand | `--text-on-brand` | `rgb(var(--accent-fg-rgb))` | Text on accent fills |

### Rules, not borders

Alpha over the paper, so a hairline stays a hairline on white, on cream, and on
a tinted row — instead of turning into a grey line that fights all three.

Subtle and strong are `--lp-rule` and `--lp-rule-strong` **exactly**. Subtle was
set at `0.10` in an earlier pass — a 30% weaker line than the landing draws,
which read as washed out on every card.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Subtle | `--border-subtle` | `rgb(13 15 18 / 0.13)` | `rgb(245 243 237 / 0.13)` |
| Default | `--border-default` | `rgb(13 15 18 / 0.22)` | `rgb(245 243 237 / 0.22)` |
| Strong | `--border-strong` | `rgb(13 15 18 / 0.30)` | `rgb(245 243 237 / 0.30)` |

### States

The landing badge foregrounds, unchanged. See the badge-pair table in §1 for
the soft fill each one belongs with.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Success | `--state-success` | `#11743c` | `#5fdc95` |
| Warning | `--state-warning` | `#8a6400` | `#e5b33c` |
| Error | `--state-error` | `#c22f15` | `#f08a72` |
| Info | `--state-info` | `#5a3fd4` | `#8b7af5` |

### Dark Mode Core

The same press run on darker stock: warm near-black paper, cream ink, the four
jewels lifted until they hold on it. Not a grey app — every neutral carries a
little warmth, the same as the light side. The landing is light-only, so this
half is ours.

- `--accent-rgb: 139 122 245`
- `--bg-canvas: #131311`
- `--bg-surface`, `--surface-1`: `#1b1b19`
- `--bg-subtle`, `--surface-2`: `#212120`
- `--bg-muted`, `--surface-3`: `#2a2a27`
- `--text-primary: #f2f0ea`
- `--text-secondary: #a3a29c`
- `--text-tertiary: #78776f`

Dark mode hover **brightens** rather than darkening — a darker fill recedes into
the surface instead of reading as pressed.

## 3. Typography Rules

### Implemented Font Families

**One family, product and landing.** Inter for everything, DM Mono for anything
that represents a path, a code value, or a machine. `landing-type.css` had
already made this argument for the marketing side — mixing Bricolage for
display, IBM Plex for body and Inter elsewhere is why its sections stopped
looking related. The same was true across the signup boundary: the app and the
page that sold it were set in different voices.

- Product body: `--font-body-family` → `var(--font-landing-sans, "Inter"), system-ui, sans-serif`
- Product display: `--font-display-family` → **the same stack**
- Product mono: `--font-mono-family` → `var(--font-dm-mono, "DM Mono"), ui-monospace, …`
- Documents: `--font-document-family` → aliases the body family

Display and body being identical is the point. **Hierarchy comes from weight,
size and tracking, never from a second typeface.** Reaching for a display face
to make a heading feel important is the move that produced three unrelated
voices in one product.

Weights: 400 and 500 carry the voice; **600 is for dense product chrome only** —
table headers, sidebar active rows, row titles. Landing deliberately stops at
500 and reserves 600 for the fake product UI inside its mockups, which is
exactly the weight the real product chrome now uses.

**Never name a webfont in a product stylesheet.** Route through the four tokens
above; a `font-family` naming a font directly in `styles/features/**` is drift.
IBM Plex Sans and Source Code Pro were removed from the font loads entirely.

Landing keeps these for its own display moments:

- Landing serif: `Fraunces`, via `--font-landing-serif`.
- Landing mono: `IBM Plex Mono`, via `--font-landing-mono`.
- Landing display: `Bricolage Grotesque`, `DM Sans`, `Playwrite TZ`.


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
| Eyebrow | `.type-eyebrow` | 12px | 500 | `wider` | Uppercase section labels, kickers |
| Eyebrow (dense) | `.type-eyebrow-sm` | 11px | 500 | `wider` | Pills, column headers, table meta |
| Eyebrow (mono) | `.type-eyebrow-mono` | 12px | 500 | `widest` | Technical/status eyebrows |
| Meta | `.type-meta` | 12px | 400 | `normal` | Counts, timestamps, helper copy |
| Body | `.type-body` | 14px | 400 | `normal` | Default UI text, descriptions |
| Body strong | `.type-body-strong` | 14px | 600 | `normal` | Row titles, emphasized body |
| Title | `.type-title` | 18px | 500 | `snug` | Card, panel, and dialog titles |
| Page title | `.type-page-title` | 24px | 500 | `tight` | Page headings |
| Display | `.type-display` | 36px | 500 | `tight` | Onboarding and hero headings |

**Weight is not a hierarchy lever.** Size and tracking already separate these
roles; stepping weight up as well double-counts it. Display, page title and
title were all set at 600–700 in an earlier pass, and setting Inter that much
heavier than the landing does is what made the product read as a different,
blunter typeface than the page it continues from. `.type-body-strong` keeps 600
because it is dense chrome — a row title inside a list — which is exactly the
job landing reserves 600 for.

The `body` baseline is **14px / 1.6**, the landing's body setting. It used to be
16px / 1.5, which is why comparable surfaces read a size looser than the page
they continue from.

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
- Tight tracking: `--tracking-tight: -0.032em`
- Snug tracking: `--tracking-snug: -0.016em`

The landing's values. Inter wants real negative tracking as it scales up —
`-0.032em` on display and `-0.016em` on titles is what makes a heading read as
*set* rather than as typed.
- Normal tracking: `0`
- Label tracking: `0.04em`, `0.08em`, `0.16em`

### Typographic Behavior

- Product `h1` and `h2` use Inter at `font-weight: 600` with `--tracking-tight`.
- Product `h3` to `h6` use Inter at `font-weight: 500` with `--tracking-snug`.
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

Sized off what the landing actually draws, counted across its stylesheets:
**8px is the most common corner by a distance** (the button), then 10px, then
7/6px, then 12px. A CTA is the same object on both sides of the signup boundary.

`14px` appears only 16 times and is the floating **header pill** — reading it as
the card corner, as an earlier pass did, set every panel a full step too round.

| Token | Value | Use |
| --- | --- | --- |
| `--radius-xs` | 3px | Brand mark bars, loader bars |
| `--radius-sm` | 5px | Checkboxes, tiny pills |
| `--radius-md` | 8px | Buttons, inputs, tabs, nav items |
| `--radius-lg` | 10px | Dialogs, popovers, table containers |
| `--radius-xl` | 12px | Cards, panels, table shells |
| `--radius-2xl` | 14px | Assistant shells, larger panels |
| `--radius-3xl` | 16px | Soft cards — templates, assistant approval cards |
| `--radius-4xl` | 20px | Softest surfaces — office/stage chrome |
| `--radius-full` | 9999px | Pills, avatars, circular buttons |

Actual components also use Tailwind utility radii:

- `rounded-md`: default buttons, inputs, tabs, nav items.
- `rounded-lg`: dialogs, popovers, table containers, cards.
- `rounded-xl`: table shells, builder rows, kanban columns.
- `rounded-2xl` / `rounded-[1.4rem]` / `rounded-[1.6rem]`: home, template, and softer dashboard cards.
- `rounded-full`: pills, profile buttons, selected floating action bar.

## 5. Depth & Elevation

**Paper, not glass.** Shadows are cast in the ink colour (`13 15 18`) rather
than neutral black, so a lifted card warms the sheet under it instead of greying
it. Sheets stay close to the page; only true overlays float.

| Level | Token | Treatment | Use |
| --- | --- | --- | --- |
| Flat | none | Rule only | Static cards, table rows, sidebars |
| Ring | `--shadow-xs` | `0 0 0 1px` ink hairline | Table shells, active tabs, small icon blocks |
| Small | `--shadow-sm` | Hairline plus 1px/2px drop | Cards, assistant strips, hover surfaces |
| Medium | `--shadow-md` | 16px blur, tight offset | Interactive card hover |
| Large | `--shadow-lg` | 40px blur plus hairline | Dialogs, popovers, sheets, selected-row bar |
| Extra Large | `--shadow-xl` | 64px blur plus hairline | Highest priority overlays only |

Dark mode inverts the hairline to warm-white alpha and deepens the drop, because
an ink hairline is invisible against dark stock.

Rule: if a surface is not floating, interactive, or selected, prefer border and background contrast before adding shadow.

### Native materials

The desktop shell puts an `NSVisualEffectView` behind the webview and sets
`[data-desktop-vibrancy="macos"]` on the document element. Under that attribute
the pod shell rail hands its background back (`--pod-shell-bg: transparent`) and
`body` stops painting the canvas, so the rail renders as a real vibrant sidebar.
`prefers-reduced-transparency` falls back to a flat fill, as AppKit does.

**This is behind `LEMMA_DESKTOP_VIBRANCY=1` and is not yet verified route by
route.** Vibrancy is only visible where the web content declines to paint, so
any full-page surface that inherited its background from `body` rather than
painting its own goes translucent — `dashboard-home-page` is the known case.
Sweep every route in the running app before making the flag the default.

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
