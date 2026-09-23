# Design

- Keep the conversation visible; stage tabs retain mounted apps.
- Use shared tokens in `src/styles/tokens.css` and `accents.css` for both themes.
  Keep the page neutral; concentrate identity color in teammate cards and marks.
- Schibsted Grotesk for UI, Newsreader for documents, DM Mono for code and
  metadata. Font weight stays at or below 500 except documented allowances.
- Pair fills with their ink tokens: `--field-ink`, `--on-accent`, `--on-ok`,
  `--on-bad`. Measure contrast in both themes; never assume white text works.
- Use Phosphor icons and local brand SVGs. Keep human and teammate identities
  distinct. Never invent user information or connected state.
- Show explicit loading, empty, error and retry states. Keep sample work labeled.
- Respect reduced motion. Provide accessible names, visible keyboard focus,
  dialog focus containment/return, Escape handling and 44px touch targets.
- On mobile, use an overlay sidebar and prevent page-wide horizontal overflow.
- Persist display preferences, not private conversations or credentials.

For visual changes, inspect 1440, 1024, 768 and 375px widths, light/dark themes,
and reduced motion. Run the checks listed in [README.md](README.md); authenticated
flows also need a live session. Design checks: `npm run check:design`.
