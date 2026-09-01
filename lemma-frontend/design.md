# Lemma Frontend Design Digest

This digest documents the current product UI in this repository. It is based on the implemented Next.js app, Tailwind v4 token setup, shadcn/Radix primitives, pod workspace screens, records/tables, flows, assistants, desk runtime, dashboard, and landing surfaces.

## 1. Visual Theme & Atmosphere

**Ink on paper.** The product speaks the landing page's language: warm near-black ink (`#2b2924`) on warm paper (`--bg-canvas: #f2efe7`), alpha hairlines instead of grey borders, and four saturated jewel tones carrying meaning. Editorial, not chrome — the palette is supposed to have an opinion.

**The landing's values, at the product's dosage.** The palette was first taken from the landing literally, and that is right for hue and wrong for intensity. A landing page is a poster: four elements, read once, scrolled past, where maximum contrast is a feature. The product is a workspace: forty elements, read for hours. Three things were toned down accordingly, and the reasoning belongs with the tokens rather than in a changelog:

- **The ink was cool.** `#17181a` is hue 264° — a blue-black — on paper at hue 90°. Dark mode had already been drawn correctly (warm ink `#f2f0ea` on warm stock `#131311`); the light side never got the same pass, and a cool black on cream is precisely what a screen default looks like. The whole ramp now rotates warm at unchanged lightness.
- **The ink was maximal.** 17.8:1 against the sheet, where black on white is 21:1 and printed stock measures about 12:1. Now 14.2:1 — still far above AAA, and it buys back the difference between "set" and "shouting".
- **The sheet was pure white.** `#ffffff` made the most-repeated surface in the product the only thing in the palette with no warmth at all, so a card read as a hole punched through to a lightbox. `#fdfcf8` is stock, and still clears the canvas by ~3.8 L*.
- **Gold was a cast, not an accent.** `--brand-yellow-soft` was six times the chroma of the paper it sits on, so even a 12% mix arrived as highlighter, and one ambient gradient in `base.css` tinted the entire viewport gold on every screen that opted in. Both are pulled toward the paper's own saturation; the ambient wash is now neutral.

This is a deliberate reversal of an earlier pass that matched the macOS system appearance. That version was native and correct and completely inert: Apple's system palette is designed to be a neutral *host* for someone else's content, so adopting it literally produces a product with no point of view. What survived from it is the part that was actually about behaviour — appearance-following, materials, control geometry, alpha-based rules.

**One voice across the signup boundary.** The app and the page that sells it are now set in the same typeface, use the same badge tones, and share a button radius. `landing-type.css` had already argued this out internally ("one family, one scale"); the same argument applies across the product line.

**Key Characteristics**

- Warm paper canvas: `#f2efe7` light, `#131311` dark. Never a neutral grey — every neutral carries warmth.
- Warm near-black ink, never pure black and never cool: `#2b2924`. The ink and the paper share a hue.
- Sheets are stock, not light: `#fdfcf8`, never `#ffffff`. Pure white survives only as text on a saturated fill (`--text-inverse`) and behind third-party logo tiles.
- Violet `#5a3fd4` carries action. Green confirms, amber delights, rust asks.
- **The mark is violet, everywhere** — see §1 *The mark*.
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
| Delight / warning | `--delight`, `--state-warning` | `#8a6400` | `#f7edd4` |
| Attention / error | `--attention`, `--state-error` | `#c22f15` | `#ffe1da` |
| Intelligence | `--intelligence` | `#d97757` | `#f2ebe6` |

Terracotta is the assistant's colour on the landing and stays the assistant's colour here.

Every foreground was picked by the landing to be legible as text on its own fill, which is why they can serve both roles without an accessible-variant split.

### The mark

The three bars are violet. Not sometimes — everywhere, in every surface this repository ships.

They had drifted into six colours: gold in the app chrome (`.lemma-mark-bar` read `--delight`), near-black behind an `!important` on the landing header, violet in the auth portal, dark green in the favicon, gold again in the PWA icon, and gold once more in the "made with Lemma" badge injected into published surfaces. The logo was the one element that never told you which product you were in.

| Where | Token / value |
| --- | --- |
| App chrome — `.lemma-mark-bar` | `rgb(var(--accent-rgb))` |
| Landing header — `.lp-brand-logo` | `rgb(var(--accent-rgb))` |
| Auth portal — `.lemma-logo .lemma-mark` | `--lemma-primary` → `--action-primary` |
| Favicon — `app/icon.svg` | `#5A3FD4` |
| PWA / desktop — `public/lemma-icon-fullbleed.svg` | `#5A3FD4` |
| Backend wordmark — `lemma-backend/public/icons/lemma.svg` | `#5a3fd4` |
| Published-surface badge — `runtime_config.py` | `#8b7af5` — on its own near-black pill |

In CSS the mark reads the accent *channel* rather than a fixed hex, so it lifts to `#8b7af5` on dark stock with everything else that carries identity. Static assets hardcode the appearance they actually render against.

**The one exception**, and it is the same exception as `--text-inverse`: a mark sitting *on* an accent fill stays `currentColor`. The SDK's `AppGate` draws it inside the violet "Login with Lemma" button, where a violet mark would be invisible. On a fill, the mark is knocked out, not tinted.

`AppGate`'s `--lap-accent` is deliberately *not* the source here — a consumer can theme it (`appearance.accent`), and the Lemma mark must not take on the host app's brand colour.

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
| Surface | `--bg-surface`, `--surface-1` | `#fdfcf8` | Cards, sheets, tables, popovers — stock, not light |
| Subtle Surface | `--bg-subtle`, `--surface-2` | `#f7f5ef` | Table hovers, inputs, secondary panels |
| Muted Surface | `--bg-muted`, `--surface-3` | `#edeae1` | Secondary buttons, selected rails |
| Brand Primary | `--brand-primary` | `#5a3fd4` | Selected states, strong identity |
| Brand Secondary | `--brand-secondary` | `#62666b` | Supporting text, chart colour |
| Brand Accent | `--brand-accent` | `#8a6400` | Delight, progress, active rails |
| Brand Warm | `--brand-warm` | `#b0842f` | Atmospheric warmth |
| Brand Coral | `--brand-coral` | `#c22f15` | Attention, human-review emphasis |
| Brand Sky | `--brand-sky` | `#d97757` | Terracotta — the assistant's colour |
| Brand Tan | `--brand-tan` | `#f0ece1` | Badge fill |
| CTA Background | `--cta-bg` | `rgb(var(--accent-rgb))` | Primary buttons |
| CTA Foreground | `--cta-fg` | `rgb(var(--accent-fg-rgb))` | Primary button text |

### Text Scale

Ink, not grey — and warm ink, not cool. Pure black is never used, and neither
is a blue-black: the ramp sits at hue ~85–90°, the same hue as the paper, which
is what makes it read as printed rather than as a screen default. The earlier
ramp was the landing's, at hue ~255–264°, and the mismatch against cream did
more damage than the contrast figure did.

Steps 2–4 hold their previous lightness exactly and only rotate warm, so the
hierarchy between them is unchanged. Only Primary also moved in lightness.

| Role | Token | Value | Contrast on sheet | Use |
| --- | --- | --- | --- | --- |
| Primary | `--text-primary` | `#2b2924` | 14.2:1 | Headings, active nav, important cells |
| Secondary | `--text-secondary` | `#63605a` | 6.1:1 | Body text, table content, nav labels |
| Tertiary | `--text-tertiary` | `#8b877e` | 3.5:1 | Metadata, labels, helper copy |
| Soft | `--text-soft` | `#aaa69c` | 2.3:1 | Placeholders, muted unavailable values |
| Inverse | `--text-inverse` | `#ffffff` | — | Text on dark or saturated fills |
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

The tab strip holds only what someone opened as work — conversations,
sections, widgets. Apps and agents are never *pinned* tabs: the sidebar rails
hold them (see below). The one exception is display-only — while the app
viewer is focused, the strip derives an ephemeral tab for the open app so it
can still mark where you are; it is never written to the working set, and
stored app tabs from before the rails are dropped at parse and at sync.

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

### The sidebar rails and pod home — colour where it means something

The shell's nav was ink-on-paper with one accent bar; the conversation surface
was the only room that spent the jewel tones. The current rule: **colour is
allowed wherever it identifies or reports, never where it merely decorates.**
Everything below reuses the badge pairs and the six `data-accent` tones — no
new hues, no new gradients.

- **Things you open are rails, places you browse through are rows.** Apps and
  agents are not nav tabs: each is a rail under its own header — apps as
  seeded marks (the identity system's square for inert things), agents as
  faces, both at 32px so a being's rich motion is on (reaching for a row wakes
  its face) — one click to the thing's own page, with a "View all" beside the
  header for the index. Data, Docs and the setup places stay rows: you browse
  *through* them rather than open one of them.
- **An empty rail does not draw itself.** The headers used to stay when a rail
  was empty, on the theory that the route would otherwise be stranded — which
  put an "Apps / View all" line above nothing at all, and on a fresh pod stacked
  two of them. A header with no rows under it is not a preserved route, it is a
  broken one. A rail appears when it has something to show, and the index route
  lives permanently in `More` instead: on an empty pod the way to apps should be
  in a fixed place. The agents rail needs no such guard — Lem always leads it. That `More` entry appears *only* while the rail is empty:
  keeping it there permanently, for a nav position that never moves, put "Apps"
  on screen twice, and on an app route the active fill landed on the buried copy
  inside `More` while the rail above it sat plain. One unambiguous answer to
  "where am I" is worth more than a position you never had to learn.
- **The places are a footer, not a middle band.** Apps, agents and recents run
  as one contiguous column — everything you open, in the order of a day — and
  Data, Docs and `More` sit pinned under them, above the account. In the middle
  the strip had no defensible reading: every other section carries a header and
  this one cannot plausibly have one, so three small rows simply dangled off the
  agents rail. A footer needs no header. The sidebar's one rule moved to that
  strip's *top* edge with it — on the bottom it introduced the history rather
  than separating the places from the rail above, which is what made the strip
  read as the rail's demoted tail. The account block takes no second rule: one
  footer, one seam. Being pinned outside the scrolling history is also what
  stops a long list of conversations pushing Data and Docs off the bottom.
- **Emphasis is spent once.** The rails lead the column, and *leading it is the
  emphasis.* An earlier pass stacked four of them on the same rows — first
  position, then a 2.75rem height against a 2rem history, then a hover wash in
  the resource's own tint, then a coloured glow under the tile — and four rules
  saying one thing about one row is what made the rail read as unfinished
  rather than important. The wash and the glow are gone; the height is now only
  what a 32px tile needs to breathe. A hue that paints whichever row is under
  the pointer identifies nothing, because in turn it paints all of them.
- **Mass follows use, so the history is not the smallest thing in the column.**
  The rail was the tallest band and recents — the part anyone actually clicks —
  was the shortest, which read as importance descending and ran exactly
  opposite to it. That gap closes from the history's end: a recent row carries
  the mark of whoever answered it and stands at 2.25rem. Apps, agents and
  recents are one band of *things you open*; Data, Docs and the setup places
  are the quieter strip of *places*. Two densities, and the line between them
  is the same line the first bullet draws.
- **A recent conversation shows who answered it.** The assistant answers most
  runs and an agent answers the rest, and a list of titles alone cannot tell
  you which — a pod's history is mostly one-word titles, so two rows both
  called "hey" were indistinguishable. The row draws the responder's mark at
  20px — the identity system's pip floor, and as small as a face reads. An
  `agent_id` that resolves to nothing falls back to the dot rather than seeding
  a face for an unknown id, which would draw a stranger. The agent page's rail
  passes no mark — every conversation there belongs to the agent whose page you
  are on, and fifteen copies of one face identify nothing.
- **Mark the exception, and let the default draw nothing.** The assistant
  answers most of a pod's history, so marking it marks everything. Putting it on
  a filled violet tile was the loud version of that mistake; taking the tile away
  and keeping the mark was the quiet version — fifteen rows, fifteen Lemma marks,
  a logo wall down the side of the app. Only an agent draws a face. The slot
  stays reserved so every title keeps one left edge, and the rows with no face in
  them carry the status dot they always had. The test generalises: **if every row
  wears the mark, the mark is decoration in identity's clothes** — count the
  instances on screen before deciding a mark identifies anything.
- **The mark says who, the pip says what the run is doing.** They are separate
  objects, because the identity system's `state` describes the *agent* and an
  agent with three runs in flight is not "the state" of any one of them. The
  pip sits on the tile's corner, ringed in the shell ground, and only when
  there is something to report: live, waiting, or recently failed — a face is
  already a mark, so a resting one needs nothing added to it.
- **The cast is people, not callable contracts.** An agent that declares typed
  inputs is *called* with arguments; an agent without them is *talked to*. Only
  the second is in the agents rail — the first belongs beside the functions and
  workflows that call it, which is where anyone would look for it. The server
  answers this with `takes_input` on the agent summary, because the list
  endpoint deliberately omits `input_schema` and a frontend filter reading that
  field would silently pass everything. The roster behind "View all" still
  lists every agent: filtering the rail is a matter of what belongs in a cast,
  and an agent that cannot be found at all is a bug report.
- **Lem leads the agents rail.** It is the responder every conversation already
  knows, so it is drawn from a *reserved* seed rather than a hashed one — the
  same creature in every pod — and links to its own page (`/ai/assistant`). The
  seeded faces cap one row shorter to make the slot.
- **The default responder has a name, because the greeting needs one.** It had
  five — "Pod Assistant" here and on its own page, "Lemma Assistant" in the
  dock and the conversation list, "Lemma Assist" in the empty state, "Pod
  assistant" in the Slack pickers, `pod_default` on the wire — for the one actor
  that answers most of a pod's history. "Assistant" could not be the settlement:
  `docs/product/README.md` fixes the product's nouns against the analytics event
  catalog, and `assistant` is not among them and appears in no journey, so
  adopting it meant amending an enforced vocabulary to add a *category* that
  competes with `agent`. A proper name needs no amendment — Lem is an instance,
  the way someone's own agent is `triage` — and it is the only form that works
  where the copy is first-person: the front door renders `Hey, I'm {label}`
  because the artwork has eyes, and "Hey, I'm Pod Assistant" was a job title
  introducing itself. Display copy only; `pod_default` and `POD_DEFAULT` stay on
  the wire untouched.
- **A being of the cast, not the trademark over it.** Lem wore the `LemmaMark`
  on soft action fill, which was wrong three ways. It put the *company's* stamp
  on one actor inside your pod, so the row that should read as yours read as the
  vendor's — in a product other people self-host. It broke the identity system's
  own law — *agency is saturated, inert is tinted* — by drawing the pod's most
  capable agent as a **mark on a tinted ground**, the treatment reserved for
  tables: a table whose glyph happened to be a logo. And a logo has no eyes, so
  the agent that runs most often was the only one that could not show it was
  running, waiting, or failed. Lem is now a `being` like every agent: same
  renderer, same upper-left light, same eyes, same state pip, same rich motion
  above 32px. The tile went with the logo — a being carries its own colour and
  needs no ground, and the tile was the last thing claiming this row was a
  different *kind* of thing from the cast it leads.
- **Spend the distinction once, on the silhouette.** Lem is told apart by one
  channel, the one `seeded-identity.ts` already says survives the shrink: all
  eight seeded bodies are convex, and Lem's is not. Colour could not do it — an
  agent may roll tone 0 too — and neither could a crest, because the band
  visible above a body is about eight units tall and detail dies there first.
  Concavity is also the one property the generator cannot reach by accident, so
  it is a guarantee rather than a low probability: a ninth convex blob would
  have left Lem one unlucky hash from a twin. The body is **appended** at index
  8 rather than carved out of the seeded range — reserving index 7 would have
  cost the roster twenty buckets (160 → 140) to identify one creature, and every
  existing agent's face is bit-for-bit what it was.
- **The recents rule stands, for a new reason.** "Mark the exception, and let
  the default draw nothing" was written because fifteen rows drew fifteen Lemma
  marks — a logo wall down the side of the app. Giving Lem a real face does not
  reopen it: fifteen copies of one face identify nothing either, which is the
  same call the agent page already makes for its own rail. The rule survives its
  original justification.
- **The kinds keep their jewel tone.** Data green, Docs amber — plus violet
  Apps, terracotta Agents and `--collaboration` Workflows wherever those
  kinds are drawn (menus, dialogs, headers) — on `ProductIcon[data-kind]` in
  every state, so a kind's colour is its identity and the pointer is still
  answered by motion (the glyph scale), never weight or fill. Setup kinds
  (connectors, settings) stay ink so they recede.
- **The active row wears the soft action fill.** `--sidebar-active-bg` is
  `--action-primary-soft`, not the sheet colour — "you are here" is the same
  light violet the model picker uses for a selected row, with the accent bar
  kept. On the resource rails the bar wears the resource's own hue (brand
  violet for Lem): the fill says *here*, the bar says *who*.
  Apps all open on the one viewer route, so the active app row is
  decided by the `page` query, not the pathname.
- **One conversation list, and it is the shell's.** The agent page grew its own
  rail of this agent's runs on the left, which put two lists of conversations
  side by side — the pod's history in the sidebar, the agent's a border away,
  same rows and nearly the same width. Two lists of the same kind of thing read
  as two sidebars, not as a hierarchy, and no amount of styling makes the inner
  one subordinate when it is the same object. The sidebar's survives because it
  is on every route.
- **The one list changes scope with the route.** Removing the rail without this
  left an agent page with nowhere at all to see that agent's runs — the dock's
  History tab is only reachable while editing, so talk mode had no list. Recents
  takes a third scope, shown and selected only while an agent's page is open, so
  the answer to "where are this agent's conversations" is the list that was
  already on screen rather than a second one. The choice is remembered against
  the page it was made on and lapses when it stops applying, and it is derived
  rather than set in an effect, so the control and the list can never disagree
  for a frame.
- **Talking is the agent page's default; editing is a button that swaps the
  panes.** The page opened in its editor — config in the main column, a dock on
  the right to actually run the thing — which optimised for the rarer act. You
  tune an agent while building it and seldom after; you talk to it every day,
  and the sidebar rail put every agent one click from every route, so this is
  somewhere people land rather than visit. `Edit` swaps which pane is big:
  config takes the column and the conversation moves to the dock. The thing you
  came to work on is always the large one, and "tune it while testing it" — the
  good part of the old layout — survives. An agent with declared inputs skips
  all of this and opens in the editor: it is *called*, not talked to, so there
  is no talk mode to default to (the same line `takes_input` draws).
- **An agent's page launches conversations; it does not host one.** Making the
  front door the empty state of a live conversation meant typing there turned the
  agent's page into a transcript, and its front door was gone until you asked for
  a new run — one surface doing two jobs and losing the first. That fought the
  workspace this product already has: conversations are *tabs*, and the tab set
  is persistent in storage rather than derived from the current route. So sending
  navigates to the conversation route with `assistantMessage` — the loud half of
  `composer-launch`, correct here because the sentence is already written — the
  run opens in its own tab, and the agent's tab is still sitting beside it. Pod
  home already worked this way; the agent pages now match it.
- **Which is what makes the page worth visiting.** Freed from being a transcript
  container it can be a home: who this is, the box that starts something, where
  else it can be reached, **what it does without you**, and a preview of what it
  has been doing. That last one is the page answering "where are this agent's
  conversations" itself rather than delegating to the shell — and a list inside a
  scrolling column is not the second navigation rail we removed, because it
  scrolls with the page instead of standing beside it. Routines earn their place
  for a different reason: a schedule is the part of an agent easiest to forget
  you set up and most surprising to rediscover from a run you did not start, so
  the home states it plainly. Neither section is an editor — they say *that* it
  runs and *what* happened; Configure is where you change it.
- **Both front doors stand on the same paper.** `.resource-page-scroll` sits on
  `--bg-canvas` because it holds cards, and a card needs the darker sheet behind
  it to read as raised. An agent's home has no cards — one column of content,
  exactly like pod home — so it takes `--pod-main-bg`. Two pages doing the same
  job on two different grounds was most of why one of them did not feel like the
  other.
- **Configure, not Edit, and in the page rather than the bar.** "Edit" names a
  mode; "Configure" names what you get — instructions, reach, schedules. Keeping
  it in the top bar meant the bar existed to hold one button on a page designed
  to have no chrome; putting it *last* then made it an afterthought on the one
  screen that owns the agent. It belongs top-right of the column — where a page
  action goes on a screen with no bar to put one in.
- **The home is 44rem, and the columns are allowed to differ.** 34rem was a
  ribbon down the middle of a very wide pane. 52rem — taken to match
  `.resource-page-column` so Configure would not shift the column — bought that
  consistency by running conversation titles nearly edge to edge. Hitting
  Configure replaces the content wholesale, so a change of measure underneath it
  is the smaller event, and the readable width wins. The description stays
  shorter still: the lists and the composer are happy filling the column, a
  sentence is not.
- **Connecting a surface happens on the agent's page, not somewhere else.** The
  connect chips linked to `/pod/:id/surfaces`, which is a *legacy redirect to the
  agents index* — its own comment says surfaces are configured from the agent
  that answers on them. So "Connect WhatsApp" bounced you to a list of agents,
  having forgotten both the platform you picked and the agent you picked it for.
  `SurfaceModal` takes exactly those two things, and this is that agent's page:
  it opens over the home with both already known.
- **The whole composer is the click target, not the field inside it.** A
  composer is chrome wrapped around a textarea, and clicking the chrome — the
  padding, the bar the send button sits on — did nothing, so the box looked
  focusable precisely where it was least obviously not. `mousedown`, not `click`,
  so the caret lands before the browser settles its own selection.
- **An agent with declared inputs gets a home too; the box that starts it is a
  form.** Every agent deserves the page that says who it is, where it is reached,
  what it runs on its own and what it has been doing. Only the invocation
  differs, and it differs *in place*: a free-text composer on an agent that is
  *called* with arguments would lie about how you reach it, so the same slot
  holds the run form instead. Saying "runs with typed inputs" and leaving the
  form a click away in the editor was half the fix — "how do I run this" is the
  question a home exists to answer. The renderer is the panel's own, headerless,
  rather than a second implementation of typed fields, required keys and JSON
  inputs. The gate sits on the home as well as the page, because the component
  must not be able to draw a chat box for something you cannot chat with.
- **Making an agent is one step, and the form is the page it makes.** Creation
  was a five-step wizard — identity, instructions, shape, access, review — which
  asked for an agent's whole contract before the agent existed. That ordering
  only held while the agent's own page was an editor you had to arrive at fully
  formed. It is a home with Configure on it now, so everything the wizard
  front-loaded has a better moment later: you tune an agent you can already talk
  to, against runs you have already seen, instead of guessing at its schema and
  its table access from a blank form. What stays is what cannot come later — a
  name, because it is the address that makes the agent reachable from outside
  Lemma; a face, because it is how it will be told apart in the rail; and a
  purpose, because the first instruction is written from it.
  Create lands on the agent's own page, which is therefore the success screen —
  there is no separate one, and the arrival notice names the two things to do
  next rather than the four the wizard used to.
- **The back link keeps its arrow at every width.** It was `hidden sm:inline-flex`
  on the whole control rather than on its label, so below 640px a resource page
  had no way back to the index it came from — in the one place that needs it most,
  since the tab strip is hidden there too and the browser's own back button was
  the only exit left. The arrow always renders; the label appears when there is
  room, and `aria-label` carries the destination when there is not.
- **What made the creation form read as a dialog was the missing header, not the
  frame.** It was built as a raised, shadowed sheet on a route with no context
  bar — a dialog impersonating a page, and a real dialog would have to render
  over an empty route, which is what Next's intercepting routes are for and not
  worth the plumbing here. The fix was the **bar**, which is what the card had
  been standing in for: a home earns no bar because it has moved its chrome into
  the page and a tab already names it, but a task you arrive at and leave from
  needs the title and the way out. With the bar naming it, the panel's own
  "Create a new agent" heading goes too — the shell owns the title.
  Stripping the frame as well went a step too far: two columns then sat adrift in
  a very wide pane with nothing holding them. It is framed again, in
  `.resource-card` — the same hairline, radius and near-stock fill every other
  resource page uses — under a real header. Elevation stays paper, not glass:
  `--shadow-xs`, the card's own, never a modal's lift. **A panel under a page
  header is furniture; the same panel without one is a costume.**
- **A lone object needs something to be level with.** The preview column was
  top-aligned, which left the face hanging above a few hundred pixels of nothing
  — one element with no relationship to anything beside it. Centred against the
  form it reads as the pair it is: this is the thing, these are its details.
- **The preview belongs beside the form, not inside it.** Two failed passes led
  here. Drawing the name and purpose *as* the greeting and description they
  become produced two headings with nothing to say they could be typed into.
  Replacing that with a plain labelled stack worked and was lifeless — correct
  and inert, which is its own failure on the screen that introduces a new
  agent. The answer is two panels: the face and the name standing on their own
  on the left, the fields doing their work on the right, in one card on the
  darker sheet.
- **An identity stores a variant, not a picture — so name the thing first.**
  `icon_url` holds `lemma-identity:<n>`; the seed is always the resource's own
  name. The face is therefore a function of *both*, and picking one on the create
  screen and then typing more of the name turned it into a different creature.
  That is the system working rather than a slip — renaming an agent changes its
  face wherever it appears — but on a form it reads as the picker losing your
  choice. Pinning the face would mean storing a seed, which changes the format
  and every consumer; the cheaper honesty is to ask for the name first, so cause
  precedes effect, and to say the rule beside the swatches. A surprise that is
  stated is a rule.
- **A choice you have to go looking for is not offered.** The eight faces sit
  inline, always, with a reshuffle beside them — hiding them behind a "change
  face" toggle made picking one a thing you had to find, and the face is half of
  how an agent is told apart in a rail of them. `distinctIdentityVariants` picks
  those eight by walking the counter and keeping one face per tone-and-form
  pair, because a choice between things you cannot tell apart is not a choice.
- **The placeholder carries an example, not a second request.** "Turns rough
  ideas into sharp, memorable pitches" shows the length and register of a good
  purpose; "What should it help the pod do?" only asks the question the label
  already asked. (A row of role templates sat under these fields for a pass and
  came out again — worth revisiting when there is a real set of them to offer,
  rather than four invented on the spot.)
- **A panel built for a dock has to be un-docked before it lives on a page.**
  Dropping the run form onto the home carried two of the dock's assumptions with
  it: a fixed height with an inner scroller, and a header. Capped at 26rem the
  form clipped "Start run" clean off, so an agent with a few fields could not be
  submitted at all; and the header, hidden with the `hidden` attribute, stayed
  laid out because the `flex` class beside it overrides the UA's `display: none`
  — 3.5rem of nothing above the first field. Hide by not rendering, and give the
  scrolling back to the page.
- **Chrome that restates what it sits on is not chrome.** The run form carried a
  "New run / Inputs" head above a stack of labelled fields — two lines saying
  what the thing under them plainly was — and on the home it was boxed in a
  border, which put an edge that says *panel* on a screen with no other panels.
  Both gone. A form is already legible as a form.
- **A page action belongs at the pane's corner, not the column's.** Configure was
  pinned top-right of a 44rem block centred in a very wide pane — which is not a
  corner, it is a point in open space with no edge to belong to, so it read as
  parked at random. It now sits at the pane's own top-right, the place the eye
  already checks and exactly where the context bar used to put it, and sticks
  there while a long home scrolls under it.
- **The test-chat dock is gone; the run form stays.** A conversational agent is
  started from its home and the run opens as its own tab, so a chat panel bolted
  to the editor was a third place to talk to one agent — with its own history
  list, its own header, and its own idea of which conversation you were in. The
  dock now only opens for an agent with typed inputs, where it is the form that
  runs it, which is the job it was actually good at.
- **A loading state promises the screen that is coming.** The editor's card
  skeleton stood in for the home, so every arrival flashed a stack of cards
  before resolving into a page that has none. The home's skeleton is shaped like
  the home: face, name, description, composer. Nothing about that frame is
  unknown while the agent loads.
- **Headroom is asymmetric, and the action row does not eat it.** The face is
  the first thing on the page and needs room above it to read as placed rather
  than flush against the pane — more so with the context bar gone, since there
  is no chrome overhead to stand off from. Configure sits inside that padding
  with a negative top margin, so a page that has it does not start lower than one
  that does not.
- **A bar with nothing in it should not draw.** The shell renders its context bar
  for every route that is not pod home, a conversation, or an app view — so a
  page that hands it no title, no back link and no actions still got a 48px strip
  of nothing. `hideContextBar` on the topbar payload lets a route that has moved
  its chrome into the page say so. Pod home was already exempt by name, which is
  why it always felt cleaner than pages that were trying to imitate it.
- **An agent's front door (superseded — kept for the reasoning).**
  Face at 64px (well past the 32px where rich motion turns on), a first-person
  greeting — the identity system already draws this thing with eyes that answer
  the pointer, so naming it in the third person on the one screen where it
  introduces itself would be the copy disagreeing with the artwork — its
  description, and a row of **"Talk to me on …"** buttons. Making it the empty
  state rather than a page of its own is what keeps the composer under it real:
  the greeting is what you see before the first message and the transcript
  replaces it after, with no navigation between and nothing to keep in sync.
  Lem is drawn from the reserved seed instead of a seeded face, the same call
  the sidebar rail makes.
- **A reach button that cannot be opened is named, not linked.** The row is a
  promise — *talk to me here* — so a dead href breaks it. Telegram and WhatsApp
  carry an open-chat convention and Resend carries an address; Slack and Teams
  reach the agent perfectly well but the surface record holds nothing we can
  build a link from, so they are drawn without an affordance rather than given
  one that goes nowhere.
- **The reach row advertises what is not connected yet.** Listing only wired
  platforms makes it a status report for people who have finished setting up,
  which is nobody on the day it matters most. This row is the one place that
  states the whole promise — *reach this agent where you already talk* — so an
  unconnected platform is the most useful thing it can show: an offer, not an
  absence. Up to three of them, after the working ones, wearing a dashed rule
  and quieter ink so the two never look equally ready. The connected links open
  outside the app; a connect chip is a route inside it and must not open in a
  new tab, or it strands the page you were about to come back to.
- **A conversation that fills a page still needs a measure.** Dropped into the
  main column with no width, the transcript and composer stretch the whole
  viewport — and a message box wider than anything anyone types into it reads as
  broken, not as spacious. Both take the same `max-w-3xl` the composer uses
  everywhere else. The empty state fills the viewport rather than sitting at its
  top, so the front door is centred against the composer instead of hanging
  above a void.
- **The agent page's bar carries the action and nothing else.** It printed a
  title and a "← Agents" link directly under a tab strip already naming the
  agent, on a page whose front door names it a third time in the greeting. The
  mechanism for that already existed: `titleOwner: 'tab'`, which also takes the
  name back on a compact viewport where the strip is hidden — the reason to use
  it rather than simply deleting the title. The conversation's *own* header had
  to go with it: hiding the shell's bar while the view underneath still printed
  the agent's name left the same duplication one row lower. A dock keeps its
  header — it is a panel among other things and has to say what it is; a page
  that a tab already names does not.
- **Lem's page is a conversation, not a description of one.** It
  was a stack of read-only cards — identity, wiring, a textarea that navigated
  *away* to start a run, and four recent conversations in a panel at the
  bottom — every one of them describing the assistant on a page where you could
  not talk to it. It now runs `PodAssistantEmbedded` with the same front door as
  its empty state, and takes the same bar and list rules as the agent pages: no
  title, no back link, no conversation panel. Its runs *are* what Recents shows
  by default, so a list here was the same list twice, a border apart.
- **A rule fixed on one page has to be swept across the pages built to match
  it.** The assistant page was written to mirror the agent page and inherited
  its second sidebar and its duplicate header, then kept both after the agent
  page dropped them — the fix landed where the complaint was and stopped there.
  These two pages are one design; when one of them changes shape, check the
  other in the same pass.
- **One list scales to one hand.** Scoping Recents rather than growing a rail is
  also what makes an agent's history reachable on a phone: the compact viewport
  renders the same `WorkspaceSidebar` inside `MobileSidebarDrawer`, so the scope
  control and the scoped list arrive with it. A page-level rail would have
  needed a second mechanism for small screens — which is exactly what the old
  agent page had, ceding to the dock's History tab whenever the layout stacked.
- **Live work breathes.** A running conversation in recents and a running row
  on home's Activity panel wear the chat status pill's ping halo. Home's
  halo is violet (live means acting); the recents halo keeps the sidebar's
  own delight-gold tone — the motion arrived, the semantics did not move. In
  recents the halo now rides the pip on the responder's mark rather than a dot
  in its own gutter; same tone, same motion, one fewer column.
- **Finished work wears the check.** A completed outcome on home carries the
  chat's soft-green check chip, not a bare dot.
- **Pod home greets with the pod's mark.** The seeded team identity at 44px
  (the size its motion turns on) beside the eyebrow, and the add-a-capability
  tile in soft violet. The composer remains the one loud object; everything
  added only spends colours that already carry meaning elsewhere.
- **Home does not offer suggested moves.** It carried up to four accent "do"
  pills built from the pod's own tables and workflows — "Review Projects", "Run
  Crm Gmail Poll Intake" — on the theory that a pod that can already do things
  should offer moves rather than only ask for them. In practice the moves a
  generator can name from a schema are the generic ones, and a row of pills
  nobody presses is a tray of colour under the one field that takes any answer.
  The launcher still builds them (`buildPodDoActions`), where someone has
  already said they want a starting point; the front door only asks.
- **A hue means one thing, and identity is not status.** Home's presence avatars
  hashed each person into four fixed tints — `--accent-rgb`, `--state-success`,
  `--state-warning`, `--text-secondary` — under a comment explaining that a
  fixed pool existed precisely so a hash would not "drift into the state colours
  and start implying an agent is failing". Three of the four were state colours,
  so two of five people wore success-green one scroll above an Activity panel
  where success-green means *Completed*. They now draw from the identity
  system's own pool (`lm-identity-hue-*`), the palette built for "a distinct
  being" and already worn by the agent faces beside them — the `hue` variant,
  not `tone`, because `tone` also sets `color` and would paint each initial the
  colour of the disc behind it.
- **A stand-in does not get to be the loudest object on the page.**
  `ResourceCover` is honest about being "the seeded stand-in for a screenshot",
  and at full strength two of them were the largest saturated areas on pod home
  by a wide margin — louder than the composer, which is supposed to be the one
  loud object. Their colour identifies nothing the app's name beneath them does
  not: the seed is the page slug. The window chrome stays (the comment defending
  it is right — pastel bars with no chrome read as a skeleton loader, the one
  thing an app card must not say), but the layout blocks sit well back, so the
  card reads as tinted paper with a hint of structure. The 16:9 box is unchanged
  and a real thumbnail drops into it untouched.
- **A row with no face keeps its dot — the dot is the list's left edge, not
  just a status.** Dropping the resting ring on the argument that "nothing is
  happening is better said with nothing" reads well as a sentence and fails on
  screen: with the ring gone, a column of titles hangs off a blank gutter with
  nothing to explain the indent, and the history stops reading as a list. One
  faint circle buys the whole column its left edge, which is worth more ink than
  it costs. The rule it violated is the general one — **a gutter must contain
  something or not exist**; what it must never be is reserved and empty.
- **The responder gutter is therefore reserved per list, not per row.** Three
  rules compounded badly: reserve a slot for the responder, draw nothing for the
  assistant, draw nothing when resting. In a pod where the assistant answers
  everything and nothing is running — most pods, most of the time — every row
  got an empty 30px indent and the history floated right of the rest of the
  sidebar behind a column that was always blank. Deciding once for the whole
  list keeps the titles aligned with each other, which was the only reason to
  reserve it, and a list with no faces in it falls back to the narrower dot
  gutter instead of paying for a wider one. The lesson is about *composition*:
  each of those three rules was defensible against the case that motivated it,
  and the bug was in what they left behind together — check a rule against the
  empty case too, and check it on screen, because this one read as sound
  reasoning in the diff twice running.
- **A mark that carries its own colour needs no plate; a monochrome one does.**
  `.surface-logo-chip` paints an opaque near-white tile under a third-party mark
  in *both* appearances, and §1 defends that as a deliberate divergence — which
  it is, for GitHub, whose black mark would otherwise vanish on dark stock. It
  is not a rule about third-party marks in general. Telegram, Slack and WhatsApp
  each carry their own colour and read on any ground, so on home's presence line
  the plate did nothing but paste three near-white tiles into a row that was
  otherwise the page's own colours — the one thing there that belonged to
  neither appearance. Those marks now sit unplated, and the negative-margin
  stack goes with the plate: an overlap only reads as a stack when each tile has
  an opaque edge to overlap onto, and without one the logos clip each other.
  Reach for the chip when the mark is monochrome, not when it is third-party.
- **Agents are not a band on home.** The composer's own agent picker is the
  selector, on home and on every conversation screen, so a cast strip beside
  the apps panel would be a third place to choose the same thing. Apps stay:
  the sidebar rail is a launcher and an app card is a destination, which is not
  the same object.

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

A **save** in a header is not a create — it is the action that view exists to
perform, and it stays `primary`. The audit reads the label to tell them apart
(`headerSlotPrimaryButtons`), and skips `/new/` routes, where the header action
*is* the form's submit.

The one-primary-per-view count could not see this: a header create is normally
the only primary in its file, so it passed while being exactly backwards. The
data table shipped a violet "New record" in its toolbar and a `secondary` one in
its empty state — the rule inverted on both ends, in the same component.

### Rows don't carry buttons

In a list, make the row itself the target. A button on every row is one CTA per
record, which is the same failure as a page with six primaries. Reserve buttons
for the one action per section, and let row-level affordances be `quiet` or a
chevron.

## 9. The Conversation Surface

The chat transcript is **turns, not logs**. The SDK's display rows are a
faithful, chronological record of everything a run did; a conversation is ask →
work → result. The boundary between the two is the pure adapter in
`lib/assistant/turns.ts` (`buildChatTurns`), and all rendering lives in
`components/lemma/assistant/assistant-turn.tsx` with styles in
`styles/features/assistant-chat.css` (tokens only — no raw type, spacing, or
radius values; the audit counts them).

### The four objects

| Object | What it is | Rules |
| --- | --- | --- |
| Bubble | Speech. Yours right in the accent badge pair (violet on soft violet), the assistant's left on the warm neutral `--bg-muted` — coloured side vs neutral side. Radius `--radius-3xl` with one tightened trailing corner (`--radius-sm`), borderless, lifted by shadow | Narration beats and answers are the *same* object — a messenger does not set chatter in a second face. Question/approval cards wear the same neutral fill: speech-adjacent objects, not panels |
| Status pill | The work, folded. Left-aligned: `✓ Worked for 9m 14s · 7 steps` settled, `● {live status}` running | One per turn that did work; never for a pure text exchange. Expanded, it is **the rail**: a left hairline with dot markers and per-step durations in tabular mono (hollow error dot for a step that failed and recovered), each row still drilling into `ToolDetailsPanel` — the history promise (PS-AGENT-010) is disclosure, not deletion |
| Doc card | The answer when it is long or structured (heading, table, ≥3 bullets, or 700+ chars — `answerIsDocument`, a pure function of the text so streaming and settled forms agree) | **Fill = a remark, outline = a document** — transparent with the hairline, same radius family and speech corner, capped at `72ch`. Real heading hierarchy (`text-lg`/`text-base` at 500, tight tracking), `leading-6`, lists with accent dashes. Short answers never see it |
| Artifact card | A file the run produced — presented (`display_resource` FILE) or written with a deliverable extension, deduped by path | Type-tinted tile (rust PDF, amber PPTX, green sheets, violet default), humanized name, size · compact path, Open chip. Video and images play inline from a short-lived file URL; build scripts and scratch files stay trace rows, or a run that wrote twelve files to make two would end with fourteen cards |

### Behavioral rules

- **Narration is speech, thinking is machinery.** Mid-run text
  (`is_intermediate_assistant_message`) streams in as bubbles and stays as
  bubbles after the run completes. `THINKING` — including its folded
  `traceNote` form — only ever appears inside the trace sheet.
- **Questions and approvals are in the chat**, where the run paused — not a
  panel that replaces the composer. While one is pending the composer refuses
  to send (`"Respond above to continue"`), preserving the old takeover's
  semantics without taking over.
- **The live turn always shows the pill.** A just-sent message with no output
  yet is a live turn whose pill reads "Thinking" — the transcript's only
  running indicator (unless a mount sets `statusPlacement="composer"`).
- **The pill moves with its meaning.** Completed, it heads the turn
  ("Worked for 16s · 3 steps" — a summary). Live, it sits at the turn's
  frontier as the typing indicator, after the newest beat — "what it is doing
  right now" is never stranded above the bubbles it is producing.
- **Width is a messenger measure.** The full-page conversation column is
  `max-w-3xl`; bubbles cap at `min(74%, 58ch)`, the doc at `min(100%, 72ch)`,
  cards/rail at `min(30–34rem, 100%)`, all fluid at 375px.
- **Only the result gets the doc card.** The flagged final answer (or, for
  unflagged history, the turn's closing run of answer text) is the only text
  eligible; a long mid-run beat is still a bubble. `documentEligible` on the
  turn item, `answerIsDocument` on the text.
- **Stacks share corners.** Consecutive assistant bubbles tighten their
  touching corners (`--radius-sm`) and pull the seam to 2px, so a run of beats
  reads as one stack. The outlined doc breaks the stack.
- **Timestamps are mono cluster stamps.** Time-only, under the user's ask and
  under the assistant turn's last beat — never a full date under every bubble.
  Day context comes from `daymark` separators ("Today · 20 Aug") between day
  groups.
- **Timestamps** sit under the user bubble only. The doc card carries its copy
  affordance on hover; bubbles are chat, and chat is not copied.

