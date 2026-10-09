# Design

- Keep the conversation visible; stage tabs retain mounted apps.
- Use shared tokens in `src/styles/tokens.css` and `accents.css` for both themes.
  Keep the page neutral; concentrate identity color in teammate cards and marks.
- Schibsted Grotesk for UI, Newsreader for documents, DM Mono for code and
  metadata. Font weight stays at or below 500 except documented allowances.
- Pair fills with their ink tokens: `--field-ink`, `--on-accent`, `--on-ok`,
  `--on-bad`. Measure contrast in both themes; never assume white text works.
- The Lemma mark is the three rising bars of `app/icon.svg`, in
  `--brand-bars`, everywhere the logo appears. Use Phosphor icons and local brand SVGs. Keep human and teammate identities
  distinct. Never invent user information or connected state.
- Show explicit loading, empty, error and retry states. Keep sample work labeled.
- Respect reduced motion. Provide accessible names, visible keyboard focus,
  dialog focus containment/return, Escape handling and 44px touch targets.
- On mobile, use an overlay sidebar and prevent page-wide horizontal overflow.
- Persist display preferences, not private conversations or credentials.

For visual changes, inspect 1440, 1024, 768 and 375px widths, light/dark themes,
and reduced motion. Run the checks listed in [README.md](README.md); authenticated
flows also need a live session. Design checks: `npm run check:design`.

The landing page is for a founder or a small team deciding whether to try
it. Its art direction is Anthropic's calm (warm ground, a serif for big type,
a lot of air), Linear's precision (real screens at one scale, the live
workspace on a dark band) and PostHog's wit (the cast, used sparingly). It
runs: the claim over a looping film, where it answers, Kit's live space, how it
works, the apps it builds, who sees what, how it learns, trust, open source,
and one way in.

- The headline is "Hire an AI teammate. Give it a space." The line under it
  says what the space is — the docs, lists and apps for its job, where your
  people work with it — so "space" never stands alone.
- The hero film shows adults at work, never children, and fills only the
  right of the hero so the words sit on clean ground. It plays forward and back
  so it never seams; with reduced motion it stays on its poster.
- The live workspace is the real sample at `/demo/landing`, put on a screen by
  the Try buttons under it. It takes the wheel only after a click: until then a
  clear layer lets the page scroll, with "Click to look around". Once clicked
  the frame is ringed; leaving it, clicking outside, or scrolling it mostly out
  of view hands scrolling back to the page. Nothing follows the scroll.
- Every other picture is a real screen from the sample workspace, captured at
  2x by `scripts/capture-landing-shots.mjs` into `public/landing/`. When the
  product changes, run the script; never edit or draw the images.
- The cast appears in four places on the landing: Kit on the live workspace,
  the three steps, the open-source picture and the closing line-up. A product
  page shows only its own teammate's face: on its screen and at its close.
- Claims stay inside what the code does ("approvals where a person should
  decide", not "approvals on everything").

Each part of a teammate's space has a page of its own under `/product/<part>`:
pages, tables, workflows, apps, memory and channels (groups joins when it
ships). The words are in `src/marketing/product-pages.ts`.

- Each page shows a different sample teammate's job (Scout's research,
  Remy's pipeline, June's imports), so no one name carries the site. The copy
  says "your teammate" or "it"; names appear only on the screens.
- Screens and the short loops come from `scripts/capture-product-shots.mjs`
  against the same sample, whose tables, pages and workflows are in
  `src/marketing/sample-spaces.ts` and `preview-fixtures.ts`.
- A part with no screen worth showing yet says its piece in words.
- A little life, and no more: things rise in once as they arrive, margin
  notes draw their arrow, loops play only while on screen, and the page's
  teammate sits on its closing band. Nothing follows the scroll, and reduced
  motion gets none of it (`lively.tsx`).

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

## Teammates and their spaces

A teammate and its space are one pod seen from two sides: the teammate is who
you talk to, the space is what you share. The app has two altitudes.

- Zoomed out, `/t/teammates`: every teammate in the organization as a card —
  its face, its job (the pod's `description`), one line of news (what is
  waiting on you, or its latest conversation) and the people in its space.
  Those that need you come first; there are never more than two groups.
- About is long, so a row of jumps (People, Channels, Taught, Remembers,
  Standing work, Hands work to, Runs on) stays at the top while it scrolls
  and marks the section in view.
- Zoomed in, `/t/{pod}/…`: one teammate's space. Its face and job head the
  sidebar and open About (`/t/{pod}/about`), which holds everything about how
  it works — people, channels, skills, standing work, the agents it hands work
  to, its model. Settings keeps what is about the space: coding agents, the
  organization's models, usage.
- The rail of faces is there at both altitudes. Its mark zooms out; a face
  zooms into that teammate. Collapsed, the rail is all that is left.

Faces tell the three apart: a teammate is its character on its own tint in a
rounded square, a person is initials in a circle, an agent is its initial on
a plain tile. The space's own agent is the teammate — named as the teammate
everywhere, never "Lem", "main bot" or "assistant". Say "space" only where the
place is the point (who can open something, where it is kept), and never bare:
"Kit's space", "its space", or "here". A bare "space" now reads as ChatGPT
Space. Everywhere else the teammate's name does the work.

### Over time

Time shows up three ways, and each gets its own words: tenure ("Hired 12
March by Priya", on About, from the pod's `created_at` and `user_id`),
reliability (run history on standing work, never called learning), and
learning (what it wrote down).

- About has "What Kit remembers" beside "What Kit has been taught". Taught is
  skills, which people write; remembers is memory, which the teammate writes
  under `/memory`, `/memory/agents/pod-default` and, privately,
  `/me/agents/pod-default`. One line a note, indexes (`AGENTS.md`) left out,
  a filled dot for a note changed this week. Shared and Personal are two
  tabs, the words and the split the Files view already uses.
- Under a reply, "Kit noted this · Pricing" appears when the turn wrote a
  memory note and the write came back successful. It is drawn from the tool
  call, not from the model saying so; the model still writes silently.
- Any message can be sent back as "Remember this": an ordinary message, so
  what follows is visible the usual way.
- Nothing here claims more than the files show. No counts of lessons, no
  "gets better" figures, no attribution ("from Priya") and no undo until
  memory has a revision history that records who changed what.

"Needs you" is one queue in two parts: workflow forms, and conversations
paused on a question or an approval (scheduled runs included). The rail
badge, the Teammates page and Home all read both. An ask left for more than a
week goes quiet: it stops counting on the rail and the Teammates page, and
Home folds it behind "N older". Any ask can be dismissed from Home, which
archives the conversation — the same put-away the chat list offers, so it
leaves every device and stays readable under Archived.

Memory folders are found by walking down from ones that exist (`/`, then
`/memory`, then `agents`…; `/me` for private notes). Listing a folder nobody
created is a 400, not an empty list, so paths are never asked for blind.

## Groups

A space's group chats are a place in it: `/t/{pod}/groups`, and one group at
`/t/{pod}/group/{id}`. Every WhatsApp group, Telegram group and Slack channel
any of its bots is in, from `podGroups`.

- A row says the platform, the agent when it is not the space's own, who has
  spoken there, who answers people outside the space, and when it last moved.
  Questions waiting on you sit above the list; their count is the sidebar's
  badge beside Groups.
- New group offers only what the space can do. A platform its own bot is not
  connected to is offered as connecting it, never as a dead option. Each
  platform lets the bot in its own way — WhatsApp's number makes the group
  and you share the link, Telegram adds it through a one-use link, Slack is
  `/invite` — and each sheet waits, visibly, for the group to arrive.
- A group's page answers what is waiting through the notification it came
  as, and the bot relays the answer. People are In the space, Not in it (a
  Lemma account; Invite opens Share), or Not recognised — which offers
  nothing, because linking somebody else's chat account would hand them its
  owner's access. Slack keeps its own history, so its page links there.
- Every member reads every group, except what the bot said to one member
  with that member's own access: the others see whom it answered, never what
  it said.
- Only the member who answers for a group's outsiders, or an admin of the
  space, switches the group or takes it over; anybody else is told who can,
  in place of the controls. An admin's change is told to that member. A bot
  also has its own switch, "Answer people outside {space}", in its channel
  settings; off, it closes every group the bot is in, and a group's page says
  so and links there.
- The composer in a conversation that lives on a chat platform starts on
  Reply in the person's own DM or email thread, and on Note anywhere others
  read along. The last choice is kept per conversation, in this browser.
- Only the sample's Marketing has groups; every other sample space opens on
  the first run.

## Contacts

The people a space's bots answer who are not in it are a place in it:
`/t/{pod}/contacts`, behind the `contacts` flag. Styles in
`src/styles/contacts.css`; words in `src/data/contacts.ts`.

- Three parts, each a thing a member does: **People** (who has written; one
  opens in a sheet with how they are known, a name, a box to write back, and
  Export / Forget), **On your website** (the space's chat, with its link and
  code to paste), **What contacts can use** (switches for contact-owned
  tables and for functions opened to contacts).
- Forms are not here. A form is a table opened to people outside: **Collect
  responses** in a table's header (`src/library/collect-responses.tsx`, words
  in `src/data/public-rows.ts`) picks who can answer and which columns, then
  gives the hosted link, the script tag, plain HTML, and "ask for a custom
  design". It reuses the space's first web chat as the door, making one named
  "Website" if there is none.
- A host id is never shown — "Signed in on your site". Every handle says who
  vouched for it in plain words.
- A signing secret is shown once, in its own sheet, with the code to paste
  after it; New signing secret shows the new one the same way.
- A follow-up that cannot go says why (unsubscribed, WhatsApp's day has
  passed, never wrote) — never a generic failure when the API gave a reason.
- A bot's setting is one select in its settings, "Private messages from
  people outside {space}", disabled with a reason on a bot that is not the
  space's own. The organization's cap sits in its Usage.

## Empty places

An empty list is most people's first sight of every place in a new space, so
it teaches the model or it does not. `src/space/empty-copy.ts` holds the words
for every place, as one set; `SpaceEmpty` draws them.

- Drawn instead of the list, never under its headings. A search or a filter
  that finds nothing is a different answer and keeps its one line: "Nothing
  matches."
- A picture of what the place will hold (`empty-art.tsx`): the page's greys
  and the one colour its kind wears in the list. Never a face — a teammate's
  face appears once per altitude.
- A short title, then one sentence on what belongs here and who makes it —
  usually the teammate, by name. Never "the bot", never a bare "space".
- At least one way in that needs no typing: a button (New page, Upload files,
  New row, New chat), a whole-sentence ask that can be sent as it stands, or
  the catalog under the place (page templates, app ideas). An ask that ends
  mid-sentence is for somebody who already knows what they want.
- Asks go in the floating chat's box and are never sent from here: the person
  finishes them, or sends them. The run that follows refreshes the space's
  lists as it ends, so what it made appears where the empty state was.
- Files take files: the empty state and the list accept a drop, and New has
  Upload files.

## The tour

What Lemma is, in one card, then where everything is — six stops, each
lighting the real control rather than a drawing of it (`src/tour/`).

- One sentence per stop. The words echo the landing page's beats; the
  category word appears only on the welcome and the rail, the teammate's name
  everywhere else.
- Offered once, on Home, in an account's first week, after any dialog already
  up. Asked for any time from the rail's help button or `?tour=1`, which go
  Home first. Never in the sample source unless asked, never in the landing
  preview.
- While it is up nothing underneath can be pressed and the app's shortcuts
  are held; Escape ends it, the arrow keys move through it, focus stays in
  the card and goes back where it was.
- A stop in the sidebar opens the drawer on a phone and unfolds a collapsed
  sidebar; below 640px the card is a sheet on the edge away from what is lit.
- When it ends, a line beside the help button says the tour and the guides
  live there.

## Place guides

How one place works — the gestures nothing on screen advertises — for
somebody standing in it (`src/tour/guides.ts`, drawn by `PlaceGuide`).

- Three layers, from most to least pushy, and nothing else: the place says
  it where it is used (a placeholder, a hint, a note under a section); a
  guide card when asked; a guide learned by trying. Never a popup on a first
  visit, never a tour per place — somebody who opened a page came to write.
- Asked for from the help menu, whose first item is the guide to the place
  on screen, or from the place's empty state. The card sits beside the help
  button, or along the bottom on a phone; nothing dims or steps, and Escape
  or a click elsewhere closes it.
- A line is a gesture, set like a key, and one sentence of what it does; four
  or five lines, the teammate by name.
- Trying is real: "Open the guide page" opens the page that explains pages
  by being one, made from its template the first time; other guides put a
  whole-sentence ask in the chat box, unsent.
- A place that already explains each of its parts gets no card: About has a
  note under every section.

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
