# Lemma Design Tokens

## Direction

Lemma should feel playfully professional: a calm B2B operating workspace with consumer-grade softness and small moments of delight. The substrate stays quiet and trustworthy. Delight appears as tiny signals: icons, active rails, progress marks, chips, focus rings, and short hover states.

The working principle is:

> professional substrate, playful signals

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

- Green is action and trust.
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
3. Prefer the 12, 16, 20, 24, and 32 pixel size steps.
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
