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

The embedded hero opens on Conversation without a separate demo header, footer,
or passive waiting summary below the composer. Keep the space for the workspace;
full-screen access lives beside the tour steps.

The four work examples follow Kit's Thursday launch: channel request, mobile
follow-up, Launch studio review, and a scheduled readiness check. Messaging uses
one phone-framed conversation; the surrounding copy names both supported channels.

All four work tabs share one sage stage, explanation column, framed preview, and
fixed stage height per breakpoint. Marketing copy describes all Lemma teammates;
named teammates appear only within examples.

Appearance includes Chat text size: Small (14px), Default (15px), and Large
(17px), with a live preview and a browser-local preference restored before paint.
Only message prose, its headings, and the composer follow this setting; navigation
and document tabs retain their type scale. On touch devices, the composer keeps a
16px minimum to avoid focus zoom.

Reply bubbles can use the full conversation column independently of chat text
size, so smaller text fits more on each line. Short messages still size naturally
to their content.

On mobile, conversation edges use a 10px gutter and replies place attribution
above the full-width body. Bubble padding is 12px horizontally; the composer
uses the same outer gutter without a second inset, retaining 44px controls and
the device's bottom safe area.

Conversation titles live in the history sidebar, with an inline Rename action.
Enter or blur saves, Escape cancels, and a failed save restores the prior title.
The transcript has no separate title row.
