# Lemma Design Tokens

## Direction

Lemma should feel like a quiet operations room: warm, precise, dense enough for real work, and calm under continuous change. The shell recedes, working surfaces stay crisp, and color appears only when it carries action, state, or data meaning. Motion explains what changed and where it came from.

The working principle is:

> quiet substrate, legible operations, purposeful motion

## Visual foundation

### Typography

- Product UI uses IBM Plex Sans at 400, 500, and 600. Default body copy is 14px/21px; compact labels are 12–13px; section headings are 16px/22px; page headings are 20px/26px.
- Bricolage Grotesque is a brand/display face only. Use it for the Lemma identity, a rare empty-state statement, or a major launch moment—not navigation, resource titles, forms, tables, or dialogs.
- DM Mono is reserved for timestamps, identifiers, live labels, compact operational metadata, and numeric readouts where tabular alignment matters.
- DM Sans is the long-form document reading face.
- Product copy uses sentence case. Uppercase is reserved for short mono eyebrows and operational section labels.

### Core palette

Light mode uses `#F4F3EF` canvas, `#EEEDE7` shell, `#F8F7F3` main workspace, and white working surfaces. Text is `#181816`, `#6F6D68`, and `#99968F`; borders are `#E8E7E2`, `#D9D7D1`, and `#BBB8B0`.

Dark mode uses `#11120F` canvas, `#161713` shell, `#1A1B18` main workspace, `#20211E` surfaces, and `#262722` secondary surfaces. Text is `#F0EFEA`, `#AAA9A2`, and `#75766E`; borders are `#2D2F29` and `#45473F`.

Muted indigo (`#5F61D8`) remains the primary product action. Operational data uses coral, amber, green, violet, blue, and neutral grey through `--chart-1`…`--chart-5`; those colors do not decorate navigation or resource categories.

### Shape, density, and elevation

- Radius steps are 4px for tiny controls, 6px for controls and rows, 8px for cards, and 10px for panels. Pills are reserved for state, filters, and avatars.
- Default shell geometry is a 240px expanded sidebar, 40px collapsed rail, 48px workspace tab bar, and 48px context bar.
- Standard rows are 28–32px. Buttons use 28px, 32px, 36px, and 40px size steps.
- Prefer border and surface contrast over shadow. Cards use no shadow at rest or hover; floating overlays use `--shadow-lg`.

### Motion hierarchy

- `--dur-feedback` (90ms): press, toggle, immediate acknowledgement.
- `--dur-hover` (140ms): hover and focus color changes.
- `--dur-control` (180ms): tabs, menus, small state transitions.
- `--dur-route` (210ms): route crossfade with at most 3px travel.
- `--dur-panel` (260ms): drawers and contextual panels.
- `--dur-shared` (420ms): shared-detail geometry expansion and return.
- `--dur-data` (720ms): charts, bars, and numeric reveals.

Use `--ease-standard` for routine changes, `--ease-emphasized` for spatial continuity, and `--ease-exit` for dismissal. Never animate polling by replaying a whole page. Under reduced motion, spatial movement and chart morphs become short crossfades.

## Token Layers

### 1. Primitive Tokens

Primitive tokens define raw material:

- backgrounds: `--bg-canvas`, `--bg-surface`, `--bg-subtle`, `--bg-muted`
- surfaces: `--surface-1`, `--surface-2`, `--surface-3`, `--surface-overlay`
- text: `--text-primary`, `--text-secondary`, `--text-tertiary`, `--text-soft`
- borders: `--border-subtle`, `--border-default`, `--border-strong`
- spacing: `--space-*`
- radius: `--radius-*`
- shadows: `--shadow-*`
- motion: `--dur-*`, `--ease-*`

These remain compatible with existing product code.

### 2. Semantic Tokens

Semantic tokens explain product meaning:

- `--action-primary`: run, create, proceed, save
- `--action-primary-soft`: quiet selected/active action background
- `--attention`: human review, destructive-adjacent emphasis, needs response
- `--attention-soft`: quiet attention fill
- `--delight`: honey accent for progress, active rails, small highlights
- `--delight-soft`: quiet honey fill
- `--intelligence`: AI/info signal
- `--intelligence-soft`: quiet intelligence fill
- `--collaboration`: channels/team signal
- `--collaboration-soft`: quiet collaboration fill

Color roles:

- Green is success and trust.
- Honey is delight and progress.
- Coral is attention and human intervention.
- Sky is intelligence and information.
- Lilac is collaboration and channels.
- Warm neutrals carry most of the interface.

### 3. Component Tokens

Component tokens are what primitives should consume:

- `--button-primary-bg`, `--button-primary-bg-hover`, `--button-primary-fg`
- `--button-secondary-bg`, `--button-secondary-bg-hover`, `--button-secondary-border`
- `--button-accent-bg`, `--button-accent-border`
- `--card-bg`, `--card-bg-hover`, `--card-border`, `--card-border-subtle`, `--card-shadow`
- `--field-bg`, `--field-bg-hover`, `--field-bg-focus`, `--field-border`, `--field-border-hover`, `--field-border-focus`
- `--chip-bg`, `--chip-border`, `--chip-fg`
- `--row-bg`, `--row-bg-hover`, `--row-border`, `--row-fg`, `--row-glint`
- `--segmented-bg`, `--segmented-border`, `--segmented-active-bg`, `--segmented-active-fg`
- `--progress-segment-bg`
- `--sidebar-active-bg`, `--sidebar-active-accent`

New shared primitives should prefer these before reaching for raw color tokens.

### Embedded surfaces

- Conversation widgets receive `lemma-widget-theme` with the public `--lemma-widget-*` token family.
- Installed app iframes receive `lemma-app-theme` with public `--lemma-app-*` variables, `theme`, and `density`.
- The browser SDK applies the app variables to the embedded document root, sets `data-lemma-theme`/`data-lemma-density`, and emits `lemma:theme` for framework code that needs to subscribe.
- Embedded apps retain ownership of their internal navigation and chrome. The host contract supplies palette, typography, radius, motion, and chart semantics; it does not inject or duplicate app headers.

## Iconography

Lemma uses Phosphor as its single interface glyph family. Product code imports
from `@/components/ui/icons`; only that vocabulary module imports
`@phosphor-icons/react` directly.

The governing principle is:

> icons are a grammar: nouns identify things, verbs perform actions, and states report outcomes

Rules:

1. One concept uses one glyph everywhere. The vocabulary module owns the mapping.
2. Use `regular` weight by default. Use `fill` for selected state, `bold` for
   very small status marks, and `duotone` only for large explanatory artwork.
3. Prefer 14px for compact rows, 16px for standard controls, 18px for prominent actions, and 24px for large resource identity. Reserve 32px for explanatory artwork.
4. Navigation and resource-identity icons are monochrome. Inactive navigation
   uses a tertiary `regular` icon; selected navigation uses a secondary-neutral
   `fill` icon while its label becomes primary, and the active rail carries the
   single accent.
5. Color is reserved for meaning that changes: success, warning, error, live
   activity, destructive actions, and other semantic state. Resource category
   never changes an icon's color.
6. Icon-only controls require an accessible name and, when the action is not
   universal, a tooltip. Decorative icons use `aria-hidden="true"`.
7. Do not use emoji, Unicode arrows, or punctuation as interface icons.
8. Keep brand marks, third-party logos, user-provided images, illustrations,
   diagrams, progress geometry, and data visualization outside the icon family.

`ProductIcon` keeps the stable resource nouns used across pods. Its `kind`
selects the glyph and its `state` selects `regular` or `fill`; identity never
selects color.

## Usage Rules

1. Use warm neutrals for the frame and surfaces before introducing color.
2. Use `--action-primary` for primary actions, not for decoration.
3. Use `--delight` sparingly for small progress/active signals.
4. Use `--attention` only when a person needs to notice or decide something.
5. Use surface and border contrast before adding shadow.
6. Use component tokens in `components/ui/*` and product primitives.
7. Avoid raw hex values in product TSX unless the surface is intentionally isolated.

## Design audit

The design-system audit (`scripts/audit-design-system.mjs`) enforces token compliance and tracks migration backlog. All enforced categories are at zero drift.

| Command | What it does |
|---------|-------------|
| `npm run check` | design audit + ESLint + TypeScript + edu-anchor checks (what CI runs) |
| `npm run design:audit` | full report: strict, advisory, informational, protected assistant |
| `npm run design:audit:ci` | strict gate + informational ratchet + protected assistant ratchet |
| `npm run design:audit:details` | line-number samples for every queue |
| `npm run design:audit:focus -- <path>` | narrow report to one `app/` or `components/` path |
| `npm run design:audit:changed` | narrow to changed/staged/untracked files |
| `npm run design:audit:queue` | ranked non-assistant migration queue |
| `npm run design:audit:changed-queue` | same queue for changed files only |
| `npm run design:audit:json` | parseable JSON output for snapshots/diffs |
| `npm run design:audit:summary` | compact JSON without samples |
| `npm run design:audit:baseline` | print current ratchet limits |
| `npm run design:audit:ratchet` | prevent informational backlog from growing |
| `npm run design:audit:assistant-ratchet` | prevent protected assistant drift from growing |
| `npm run design:audit:test` | validate baseline loading and reporting |

The baseline is stored in `scripts/design-audit-baseline.json`.
