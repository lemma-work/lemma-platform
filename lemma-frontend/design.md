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

Every teammate has an Apps tab, including before the first app is built. Existing
apps stay reachable above an idea catalog. Personal, Marketing, Product, Sales and
Engineering each show four distinct illustrated app ideas, in a responsive card
grid. Example previews are labeled. Build actions use the current teammate name
and prepare an editable composer draft without sending it.

Messages expose Copy on hover or keyboard focus (always on touch screens). Code
blocks, quotes and tables have separate copy controls with success or failure feedback.
Email links use the configured mail handler in the current browsing context.

## Desktop

The workspace also runs inside the Lemma desktop app, which loads it from a
local or hosted origin. Everything the app adds lives in `src/desktop/`, and all
of it renders nothing in a browser.

- **One door to the shell.** Call it only through `invoke` in
  `src/desktop/bridge.ts`, which accepts the commands in `WORKSPACE_COMMANDS`
  and nothing else. Each one must be granted in
  `desktop/capabilities/workspace.json` and registered in `desktop/src/app.rs`;
  `tests/desktop-ipc.test.ts` reads both. Never read `__TAURI__` elsewhere.
- **Gate by what you need.** `isDesktop()` for "in the app at all" (hosted or
  local); `desktopBridgeAvailable()` for commands that act on this installation
  (local deployment and the shell). Read either through its hook
  (`useIsDesktop`, `useDesktopBridge`) during render, so the server's "browser"
  answer is reconciled rather than kept.
- **Clipboard.** The local workspace is `http://*.localhost`, not a secure
  context, so `navigator.clipboard` is absent there. Always copy with
  `copyText` from `src/desktop/clipboard.ts`, and keep the call site's
  success and failure feedback.
- **External links.** Open with `openExternal`; for a URL that arrives after
  an await, `openExternalWhenReady`. The shell decides where it lands: a pod
  app gets its own window, anything else the system browser. Never treat a
  `null` from `window.open` as "blocked".
- **Report, don't prompt.** This computer connects itself; the Models page
  shows one ranked state with Try again only after a real failure. Background
  work (the sandbox download) gets one quiet corner notice, never a modal.
- **Name the machine.** "This Mac", "This PC", or "This computer" from
  `this-computer.ts`; never "this Mac" on every platform.
- **Pod apps on macOS** open in their own window where a frame would load
  signed out (`crossSiteFramesCarryCookies`), with a panel in the frame's place.
- **Settings from the shell.** The menu and tray raise
  `lemma:open-settings` with `{ section }`; the shell opens Settings there.
  An optional `focus` names the part to open at (Advanced's Google form).
- **This Mac settings.** A third group in Settings, after You and the
  organization, named with the machine's noun: Overview, Server setup,
  Coding agents, Sharing, Updates. Only in the app's own window, on a local
  install, on this installation's loopback origin (`thisMacAvailability` in
  `this-mac.ts`); a browser or a shared-address visitor sees no hint of it. Each
  setting is one row — its name, one line of what it does, its control — in
  `.thismac-row`; choices reuse `.theme__modes`, lists `.mgroup`/`.mrow`,
  fields `.field`/`.check`. No environment-variable names in copy.
- **Server setup is by capability.** One card per thing the server can do,
  with its status (Ready, Needs setup, Optional), one line of what it
  unlocks, a Test, and where to get its keys. Only the AI model is required;
  a first-run checklist offers the rest once, and Settings shows a dot beside
  Server setup while the model is missing.
- **Configure at the point of need.** A connector or channel missing this
  computer's OAuth app or bot shows "Set up on this Mac"
  (`SetUpOnThisMac`), only while that form is empty; a run that failed for
  want of a model offers `SetUpAiModelLink`. Models suggests local
  model servers it found answering, rather than asking for a URL.
- **Consent belongs to the shell.** Public sharing, repair and update install
  are confirmed natively by the shell; the page never draws its own "are you
  sure" for them.
- Desktop styles live in `src/styles/desktop.css`, built from the Models
  page's own pieces, under the same token and weight rules as everything else.
