# Parity with the web

Everything the web does (`lemma-frontend`: the `/t` workspace and the `/o`
frame), and where it lives on the phone. Parity is done when every row says
**done**.

- **done**: on the phone, designed for it, checked on a device against the local stack.
- **web**: reachable, but it opens the web's page inside the app.
- **todo**: not on the phone at all.

Each area has its own folder (`src/areas/<area>/`): reads and writes
(`source.ts`, `live.ts`, `sample.ts`), hooks, routes, and its tab on a Lemma's
page (`tab.tsx`). Its screens live under `src/app/(in)/`. Work on an area
edits its own section below, its own folder and its own screens, and nothing
else. The files listed under **Shared** belong to no area. Change one only
when the area truly needs it, and say so in the commit.

**Out of scope:** what only the desktop app has: "This Mac", pairing a
computer, app updates, disk usage.

**Shared:**
- `src/data/*` (Home, Needs you, the Lemmas list: the core)
- `src/session/*`
- `src/ui/*`
- `src/app/_layout.tsx` and `src/app/(in)/_layout.tsx`
- `src/app/(in)/lemma/[podId]/index.tsx` (a Lemma's page, which only renders area tabs)
- `src/areas/index.ts` and `src/areas/use-area.ts`

## Built for every area (use these; do not rebuild them)

| Need | Use |
|---|---|
| Read a file's text, save a file to the phone, share it | `src/data/io.ts`: `readText`, `saveToPhone`, `shareFile` |
| Upload from photos, camera or files | `src/data/io.ts`: `pickPhotos`, `takePhoto`, `pickFiles`, `uploadFile` |
| Show an image, video or PDF | a signed URL from `files.getUrl`; no headers needed |
| Live updates (a group, the inbox, a conversation's run, a table's rows) | `src/live/pod-live.ts`: `useLiveTopic`, `useLiveStatus`, `pollWhenDown`; topics in `lemma-backend/docs/modules/live.md` |
| A widget, or any page on the API's origin that speaks the widget messages | `src/ui/web-content.tsx`: `WidgetFrame`, `EmbedFrame` |
| A form: a workflow's, a table's record | `src/ui/form`: `Form`, `fromSchemaFields`, `fromRecordFields`; the SDK builds the payload |
| A sheet from the bottom | `src/ui/sheet.tsx` |
| A place not built yet | `src/ui/coming.tsx`: `ComingTab`, `ComingScreen` |
| Colours that work dark | tokens in `src/ui/tokens.ts`, never a literal |
| One area's source, live or sample | `useArea("<area>")`, keyed by `useCacheKey()` |
| Live data to check against | `scripts/seed-local.mjs` on a local stack (README) |
| Files in any chat: photo grid and viewer, document card, sheet rows, audio, attach tray, grow from a card | `src/ui/files-in-chat` (no chat-only assumptions) |

A widget draws in Albert Sans: the frame puts the app's two font files into the widget's page (`src/ui/widget-font.ts`).

## Connections (main, after the areas)

Main moved every connector call onto a pod connector (#1039, #1059): a
schedule, a workflow's start, a bundle and a place name a connector, and the
connector decides whose account runs. The phone follows in every screen that
named an account (the connector card's connect link, turning a workflow on,
copying a schedule, installing a bundle, connecting a place).

| Web | Phone | Status |
|---|---|---|
| A Lemma's Connections page (connect, reconnect, share, "Use for everyone as", coverage, ask members) | opened on the web from where it is needed | not built |

## A. Chat thread (`src/areas/chat`, `src/thread/*`, `(in)/chat`, `(in)/new-chat`, `src/ui/ask-box.tsx`)

The conversation runs on the SDK's own `useAssistantSession`, the hook the web
runs, so streaming, reattaching, stop and retry follow the same rules
(`src/thread/use-chat.ts`). Sample data runs the same screen over a sample
engine; `src/areas/chat/sample-chats.ts` has a chat for each state below.

| Feature | Web | Status |
|---|---|---|
| Assistant text: markdown, tables, code, quotes | `thread/markdown.tsx` | done |
| Copy a message, a code block, a table | `thread/markdown.tsx` | done (hold a message for Copy; code, tables and quotes carry their own) |
| Your own message as markdown; Sending…, Not sent with Retry and Edit | `thread/turn-row.tsx`, `pending-sends.ts` | done |
| Chips: note to the Lemma, a relayed answer, a guest | `thread/turn-row.tsx` | done |
| Widget inline, with live data | `thread/widget-view.tsx`, `embed-preview.tsx` | done (a compose fills the box, never sends) |
| Table or query resource as rows | `thread/data-view.tsx` | done (8 rows by 6 columns; Open goes to the table) |
| File resource inline (image, markdown, text), opening the Files viewer | `thread/file-view.tsx` | done (Open is `filesRoutes.file`, area C's screen) |
| App resource | `thread/resource-card.tsx` | done |
| Browser resource: Watch until it ends | `thread/resource-card.tsx` | done |
| Agent, workflow, schedule resources as links | `thread/resource-card.tsx` | done (to area D's routes) |
| Tool cards: sources and citations, terminal, browser, image, file, task, connector, wait, ask a teammate | `thread/tool-card-view.tsx`, `tool-cards.ts` | done |
| Memory notes ("noted this"), opening the note | `thread/memory-notes.ts` | done |
| Plan card | `thread/plan-card.tsx` | done |
| Approval: once, for this chat, deny | `thread/interaction-card.tsx` | done |
| Questions: every step, multi-select, Other, answer by typing | `thread/interaction-card.tsx` | done |
| Open question pinned above the composer | `thread/interaction-dock.tsx` | done (folds while the draft is long, or by hand) |
| Sign-in card, opening the sign-in page | `computer/sign-in-pane.tsx` | done (the web's `/sign-in-to-site` page, in the app) |
| Run states: working, failed with try again, no model, stuck with reload | `thread/transcript.tsx`, `stuck-run.ts`, `model-setup.ts` | done |
| Runtime notes, notices, day separators | `thread/transcript.tsx` | done |
| Earlier messages, jump to newest | `thread/transcript.tsx` | done |
| Queued messages as chips, take one back | `thread/queued.ts`, `composer.tsx` | done |
| Live updates over the pod socket, not polling | `lemma-sdk/react` `useAssistantSession` | done (the run streams over the socket; the `conversation` topic is held while a chat is open; REST every 15 s only while the socket is down) |
| Composer: attach from photos, camera, files | `thread/composer.tsx`, `attachments.ts` | done (into the chat's `pod_cwd`, named in the message as the web names them; also from the new-chat box) |
| Composer: model picker | `thread/model-picker.tsx` | done |
| Composer: drafts kept, Stop, filled by a widget's compose | `thread/drafts.ts`, `compose-bridge.ts` | done (drafts are kept on the phone and dropped on sign out) |
| Reply choice in a chat that also lives on a chat platform | `thread/use-channel-reply.tsx` | done |
| Rename, archive | `conversations.update` | done |

### Design v2 (the chat with a Lemma, and files in a chat)

From `design-v2.md` (approved 2026-10-24): "Chat" and "Files in a chat".

| Design | Where | Status |
|---|---|---|
| The Lemma's colour carries in: header on its wash, Send and the buttons and links in its ink | `thread/look.tsx`, the chat and new-chat screens, `ui/ask-box.tsx` | done |
| Live step line under the newest message, breathing dot in the Lemma's ink; the face's violet breathing ring | `thread/run-line.tsx`, `steps.ts`, `breathing.tsx` | done |
| The line folds into "Worked for 1m 6s · 5 steps ›"; a Drawer "How {Lemma} got there" with each step's time | `thread/run-line.tsx` | done |
| Replies fade in word by word; follow-the-newest kept | `thread/reveal.ts`, `StreamingMarkdown` in `ui/markdown.tsx` | done |
| Approvals and questions rise above the composer on the spring | `thread/dock.tsx` | done |
| Held for ~1 s when consequential (money, outside the Lemma, deleting), one tap otherwise; "Not this one" one tap | `thread/consequence.ts`, `hold-button.tsx`, `interaction.tsx` | done |
| Questions with large choice buttons | `thread/interaction.tsx` | done |
| Widgets and tables grow to the whole screen from their card, and shrink back | `thread/resources.tsx`, `ui/files-in-chat/grow.tsx` | done |
| A document, a page or any file grows into Files' reader as it opens, and shrinks back on return | `ui/files-in-chat/grow.tsx` (`useGrowInto`) | done |
| Attach tray: + turns into ×; camera, the latest photos one tap each, files | `ui/files-in-chat/attach-tray.tsx`, `recent-photos.ts` | done (the latest photos need the APK with `expo-media-library`; without it the tray offers the photo picker) |
| Photos as a grid (one large, two small, "+N"); whole-screen viewer: swipe, pinch, "1 of 5", share, swipe down | `ui/files-in-chat/photo-grid.tsx`, `photo-viewer.tsx` | done |
| Documents as cards: first page, name, kind · pages · size | `ui/files-in-chat/document-card.tsx` | done (the first page is the server's render, `pages/page_0001.jpg`) |
| Spreadsheets and CSV: first rows and the row count | `ui/files-in-chat/sheet-preview.tsx` | done (a CSV is read here; a spreadsheet from the server's converted markdown) |
| Audio: bars with play, played in place | `ui/files-in-chat/audio-row.tsx` | done (the bars are drawn from the file's name, not its sound) |
| A person's attachments drawn as files, not as a list of paths | `thread/message-files.ts`, `view.tsx` | done |
| No swipe to reply | | done (none) |

Known, and why:
- A widget's own links open in the phone's browser; one that asks for a new
  chat opens the new-chat screen with the words in its box.
- An image the Lemma looked at on its computer (`view_image` of a workspace
  file) is named, not shown: reading it would start that computer, and the
  phone has no computer pane yet (area G).
- Copy uses React Native's own clipboard module; `expo-clipboard` is the
  long-term home once a native build adds it (`src/thread/clipboard.ts`).

## B. Tables and records (`src/areas/tables`, `(in)/lemma/[podId]/table`, `record`, `edit`, `collect`)

| Feature | Web | Status |
|---|---|---|
| A Lemma's tables | `space/all-view.tsx` | done (search past 8 tables) |
| Table view: automatic layout (board, checklist, timeline, cards, chart, feed) or plain rows | `library/library.tsx`, `forms.tsx` | done |
| Search, filters, sort, columns shown | `library/filter-bar.tsx`, `filters.ts` | done |
| Row count, paging | `records.list`, `datastore.query` | done |
| Record page, with linked rows both ways | `library/record-view.tsx` | done |
| New, edit, delete a record | `library/record-editor.tsx` | done |
| Inline edits in a layout (tick, move on a board) | `library/forms.tsx` | done |
| Collect responses (public form), behind the `contacts` flag | `library/collect-responses.tsx` | done |
| Live rows over the pod socket (`records` topic) | not on the web's table view | done |

How it is built:
- The layout is chosen by the web's own code, imported where it lives
  (`@web/library/profile`, `filters`, `reading`, `record-cache`,
  `relations`, `@web/data/public-rows`; see `web-imports.js`), so a table
  comes out the same shape on both.
  `layouts.ts` is the phone's own reading on top (tested in `tests/tables.test.ts`);
  `views.tsx` draws each layout.
- Drawn for a phone: a board's piles stack, each card with its mover; plain
  rows scroll sideways; holding a row opens, edits or deletes it; the
  editor is a screen of its own (`edit/[table]`), not a dialog. Two small
  departures: a layout leaves out a link's id beside a row (the row's page
  names what it links to), and a status the schema fixes (an ENUM) offers
  its own options and no "Another…" the table would refuse. A foreign key
  is picked from the other table's rows, where the web asks for the id.
- Not carried over, on purpose: the web's Full screen (a phone's screen is
  the whole view already), and the grid's row-details dialog (every row has
  its own page).
- The `contacts` flag (`src/session/features.ts`) is read where the web reads
  it: `/site-config.js`, then PostHog through the web's `/ingest` proxy. For a
  local stack, `EXPO_PUBLIC_LEMMA_FEATURES=contacts npx expo start`.
- Words started for the Lemma ("Ask Support to fill it in", a custom form
  design) open a sheet to finish them, since the phone's chat sends what it is
  opened with; the web puts them in the chat box instead.
- Sample data (`sample.ts`): Support has `tickets` (board), `customers`
  (cards) and `replies` (reading view), linked both ways; Launch has
  `launch_checklist` and `releases` (timeline); Books has `invoices` (plain
  rows, two pages) and `daily_revenue` (chart). Writes stay in memory.

## C. Files, pages and docs (`src/areas/files`, `(in)/lemma/[podId]/folder`, `(in)/lemma/[podId]/file`)

| Feature | Web | Status |
|---|---|---|
| Browse shared files and your own `/me` | `library/library.tsx` | done (Files tab: Shared with Pages first, Mine; a folder screen) |
| Upload files and photos, new folder | `library/library-writes.tsx` | done (photos, camera, files; a name taken is replaced, as on the web) |
| Rename, move, delete, retry processing | `library/library-writes.tsx` | done (hold a row; Move picks a folder; "why it could not be read") |
| Search files | `files.search` | done (names as you type, then what is inside files) |
| View markdown and text | `thread/file-view.tsx` | done (a page opens in the editor; HTML in it, read only, as on the web) |
| View images, video, audio | `thread/file-view.tsx` | done (pinch and double-tap zoom; `expo-video`, `expo-audio`) |
| View PDF | `thread/pdf-preview.tsx` | done (pdf.js in a WebView, from bytes the app reads) |
| View HTML | `thread/file-view.tsx` | done (sandboxed frame, scripts only, in the app's theme) |
| Office and other files: open in another app, save | `thread/save-file.ts` | done (Android: the system's viewer and a folder you pick; iOS: the share sheet) |
| Edit a page: slash menu, live table views, embeds, images | `thread/document-editor.tsx`, `docpages/editor/*` | done (the web's editor itself, built by `webview/`; no drag handle on a phone) |
| Comments on a page, resolve, ask the Lemma | `docpages/comments/*` | done (a sheet; @mentions wake an agent or tell a person) |
| New page from a template | `docpages/templates.ts` | done |
| Share links: public, expiring, revoke | `thread/share-dialog.tsx` | done (the Lemma's own link, a public link with bounds, live links turned off) |

How the editor runs: `lemma-mobile/webview` bundles the web's `document-editor.tsx`
and its extensions from `lemma-frontend/src` into one HTML page
(`src/areas/files/web/editor-page.generated.ts`). The page never reaches the
network; every read and write it makes is a call the app answers with its own
session (`web/protocol.ts`). After the web's editor changes, rebuild it
(`cd webview && npm ci && npm run build`); `tests/web-copies.test.ts` fails
until then.

## D. Work (`src/areas/work`, `(in)/lemma/[podId]/workflow`, `run`, `schedule`, `agent`)

A Lemma's Runs tab holds the web's Workflows page, its Standing work, its
Agents and the `/o` frame's X-ray, a chip each (Workflows · Waiting on you ·
Running · History · Schedules · Agents · X-ray); `workRoutes.runs(podId, view)`
opens it on one. The Plan tab is the `/o` frame's plan.

| Feature | Web | Status |
|---|---|---|
| Workflows: list, waiting, running, history | `space/workflows-page.tsx` | done (Runs tab; a workflow's row ends violet when it waits on you or runs) |
| Workflow: steps, run now, turn on for you or the Lemma | `space/workflow-page.tsx` | done (the runs still going a pile per step; how it starts and turning it on; the steps as an outline; every run) |
| Run: step tree, each step's chat, cancel, retry, rerun | `space/run-page.tsx`, `run-board.tsx` | done (decisions and loops nested; an agent's step opens its chat in the chat screen) |
| Workflow forms, answered in place | `workflow/form.tsx`, `waiting-inbox.tsx` | done (on the run, in a sheet from Waiting on you, and from Needs you) |
| Schedules: status, runs, pause, who sees it, retry, copy, events | `schedule/standing-work.tsx` | done (health and failure streak, firings with retry, skipped events, a copy of your own, a new schedule) |
| Agents: list, page, chats with it | `space/agent-page.tsx` | done (a box that starts a chat with that agent; what it can do, where it is reached, what uses it) |
| Agent: edit instruction, model, effort, max tokens; delete | `stage/agents-view.tsx` | done (as `allowed_actions` allows; the model's own settings, or a coding agent's options) |
| Plan (`/plan.md`): read, edit without overwriting another's change, draft, ask the Lemma | `outcomes/outcome.tsx` (branch) | done (line by line; a save refused when the file moved on) |
| X-ray: agents, schedules, tables, apps, skills, MCP address | `outcomes/outcome.tsx` (branch) | done (each piece opens its own screen; where people reach it opens Settings) |

How it is built:
- The web's own pure modules (`web/`: runs, run tree, workflow shape, board,
  turn-on, schedules, agents, runtimes) read every payload, so a run, a
  schedule's health and an agent's permissions say the same on both;
  `tests/work.test.ts` fails when the web's copy moves on (README there).
  The plan is the `/o` frame's own code, copied from its branch.
- Live as the web is: a run going is pushed whole by its own stream
  (`/workflow-runs/{id}/stream`, through `expo/fetch`) and read again only
  while that stream is not arriving; run lists are read again while
  something in them moves. Neither while the screen is behind another.
- Drawn for a phone: the board's columns stack; a run's steps are one line
  each, opening to what they produced; the workflow's outline is one spine
  that never scrolls sideways; editing a plan line opens it in place.
- Words: the web's lines that name the pod or the space are said the
  phone's way (`model.ts`).
- Shared files changed for it: `src/data/live.ts` (a form waits as `HUMAN`
  on the wire, which the queue dropped), `src/data/model.ts` (a form and an
  automation's run open the run screen), `(home)/needs.tsx` (a form is
  answered in a sheet).
- Sample data (`sample.ts`): Support has a run waiting on your form, one
  running, one failed where it can be retried, a cancelled one, a loop and a
  decision; a schedule the breaker stopped, a paused one, one someone else
  shared with a filter; an agent you may edit, one you may only read, one
  that takes arguments; a plan. Launch and Books hold the forms Home asks
  for. Writes stay in memory.

## E. Chats, groups, people, contacts (`src/areas/chats`, `(in)/chats`, `(in)/shared`, `(in)/lemma/[podId]/thread`, `group`, `new-group`, `contacts`, `contact`)

A chat that lives on a channel (someone's own chat with a Lemma, a group made
here, a group mirrored from WhatsApp, Telegram or Slack, a contact's) runs on
the web's own channel modules, imported where they live
(`@web/channels/*`, `@web/data/contacts`; see `web-imports.js`).
The phone's own reading on top (the chat list, what holding a message offers,
the contact strip, what is on screen) is `model.ts`; the screens are drawn
for a phone in `chat-view.tsx`, `line.tsx`, `composer.tsx`, `thread-view.tsx`,
`details.tsx`, `list.tsx`, `contacts.tsx`.

| Feature | Web | Status |
|---|---|---|
| All chats across Lemmas | `outcomes/chats.tsx` (branch) | done (busiest Lemma first, the first few of each, More pages its whole history; Waiting on you per Lemma) |
| A Lemma's chats | `space/chats-page.tsx` | done (Chats tab: search, the web's filters, every chat with it and every shared chat, Older chats pages, Archived chats; hold a shared chat to mark it read) |
| Search chats, full history | `thread/all-conversations.tsx`, `channels/search.tsx` | done (titles searched on the server; "Said in shared chats" opens the chat at that message, or its thread) |
| Group page, live | `channels/channel-page.tsx`, `use-channel-live.ts`, `use-presence.ts` | done (the `channel` topic: new, edited and deleted messages, typing, an answer being written, read marks; days, a New mark, Jump to unread, earlier pages, opened at one message; who has the app open on the faces) |
| @mentions in a group | `channels/composer.tsx`, `mention-picker.tsx` | done (typed `@` or the @ button slides up a strip of faces; a person picked is a chip while their name is in the words; a Lemma picked is asked by the whole box; in an edit too) |
| Threads | `channels/thread-panel.tsx` | done (a drawer over the chat, half height and dragged to full, "Reply in thread" at its foot; its address opens the chat with it up; replies paged; typing in the thread; a mention in it is read there) |
| Edit, delete, share a message; retry delivery | `channels/line.tsx`, `parts.tsx` | done (hold a message: reply, copy, edit, delete or remove for everyone, share an answer kept to you; Try again on a send that gave up; files shown or handed to another app) |
| New group, join, leave | `channels/new-channel.tsx` | done |
| Group details: members, agents, roles, settings | `channels/chat-details.tsx` | done (notifications, mute and where to hear of it; about; people, owners, add and remove; who answers and when; connected; public or private, archive, delete) |
| Assign to me, agent answers on or off, mark read | `channel-page.tsx`, `contact-controls.tsx`, `agent-switch.tsx` | done (the contact strip's Take over and Hand back; assignee, status, hand-back hours and the Lemma's switch in details; read as you see it, or Mark read on a held row) |
| WhatsApp group and Telegram link sources | `channels/source-sheets.tsx`, `source-settings.tsx` | done (New: start a WhatsApp group and share its link, add the Lemma to a Telegram group or a Slack channel, each waiting for the group to arrive; a group's settings under Connected) |
| Waiting on you (the bell) | `channels/waiting-panel.tsx`, `form-answer.tsx` | done (a section at the top of the Chats tab and All chats, kept by the `inbox` topic: new messages in your own chat, questions answered in place, mentions) |
| People in a Lemma | `outcomes/people.tsx` (branch) | done (People tab: everyone in it, you first, who has the app open, their role) |
| Contacts: inbox, people, setup; rename, forget, export, follow up | `space/contacts-page.tsx`, `channels/contact-queue.tsx`, `space/contact-bots.tsx` | done (on the People tab for whoever may see them: who waits on a person; the queue's four views and status; contacts; setup with whom each number answers, web chats, auto-reply and what contacts reach; one contact's screen) |

Drawn for a phone, and where it departs from the web:
- The bell is not an icon with a panel: what waits on you heads the Chats tab
  and All chats, where a question is answered in place.
- A thread is a drawer over the chat and a chat's details a screen of its
  own, not panes beside it. Holding a message stands in for hover tools.
- A WhatsApp group's link is copied, sent with the phone's share sheet, or
  sent on WhatsApp or by email; there is no QR code to scan on the phone that
  would be scanning it.
- The Contacts place is offered to whoever may see contacts
  (`podPermissions.me`), as the web has done since #1014; the `contacts`
  flag gates only Collect responses (B) and the organization's cap (F) there.
- Copy names a Lemma by its name and calls what is said a message: where a
  web sentence says channel, line, agent or bot, the phone says it again
  (`model.ts`), and a test holds that.
- All chats reads each Lemma's counts on a poll rather than holding a socket
  open per Lemma; a Lemma's own Chats tab and an open chat are live.

Design v2 (`room.ts` for what it works out, tested in `tests/chats-room.test.ts`):
- People are discs with initials and Lemmas their free-standing character
  everywhere in a chat; a Lemma's message has a 2px bar in its own colour,
  from the same face id it is drawn with (`faceIdOf`: the Lemma's own for its
  front agent, one of its own for another). "X is answering…" breathes.
- The header stacks who is in the room (7px overlap, Lemmas first, never the
  reader while anyone else is in it), with the title and the topic.
- Picking a Lemma from the strip outlines the box in its colour (1.5px),
  tints Send and says "ask {Lemma}…"; the words go out led by "@{Lemma}", so
  the server sees the same mention the web sends.
- A WhatsApp, Telegram or Slack glyph sits by the name of whoever wrote from
  there; your own reply that didn't arrive says "Not delivered to WhatsApp ·
  Try again" in amber (a tap on the words says why). One that arrived says
  nothing; one on its way says so quietly.
- Who is typing shows above the box with a breathing dot.
- Files in a group message are drawn by the chat's pieces (`ui/files-in-chat`):
  pictures sent together as one grid that opens whole screen, a recording played
  in place in the Lemma's colour, anything else a document card that hands the
  file to another app (a message's file has no reader of its own on the server).
- The composer attaches from the chat's tray (camera, the latest photos, files),
  the + turning into an ×, as in a chat with a Lemma. Checked on the emulator in
  sample data: Customer care's three photos as one grid, the voicemail row, the tray.
- Checked on the emulator (2026-10-24), light and dark: the local stack's
  Launch team in Support (the header, the bar, the strip, asking Support and
  its answer arriving, the thread drawer at half and full, a reply in it,
  closing by flick and tap) and sample data (launch-week's stack of two
  Lemmas and two people, asking Researcher, typing in the room and the
  thread; Customer care's WhatsApp glyphs, "Not delivered to WhatsApp", why,
  and Try again).

Checked on the emulator against the local stack and in sample data, light and
dark. Not checked, and why:
- Tapping "Open Telegram" leaves the app for Telegram; the sheet, its link and
  its wait for the group were checked in sample data. iOS was not run.
- The local stack refuses a new web chat ("Web chat is switched off on this
  deployment"), and says so on the phone; making one was checked in sample data.
- The server refuses the last person leaving a private group ("delete it
  instead"), and the phone shows its words; leaving with others in it is the
  same call.

Sample data (`sample.ts`): Support has Ada's own chat on WhatsApp, "Customer
care" (mirrored from a WhatsApp group, with a contact in it, a question
waiting on Ada, a thread, a removed message, a send that gave up, an answer
kept private, three photos and a voicemail, a mention), four contacts, thirty older chats, and
its contacts' setup; Launch has "launch-week" (private, its Lemma answering
every message, an approval and a form waiting on Ada, an edit, a thread, a
mention) and "Press friends" on Telegram. Writes stay in memory.

## F. Settings and admin (`src/areas/admin`, `(in)/account`, `(in)/settings`, `(in)/lemma/[podId]/settings`)

A Lemma's settings are its Settings tab (`admin/tab.tsx`): what it is for and
who started it, then a row per part with what is true there now (the web's
About tiles as rows), and Delete last. Each part opens a screen of its own,
`(in)/lemma/[podId]/settings/[section]`. The account's and the organization's
are rows on the account screen, `(in)/settings/[section]`.

| Feature | Web | Status |
|---|---|---|
| A Lemma's name, description, icon | `space/about-page.tsx`, `stage/hiring.tsx` (face) | done (a photo uploaded to `/icons/upload`, one of the cast, or its own face) |
| Add people, who can join, join requests | `shell/add-people.tsx`, `who-can-join.tsx`, `at-the-door.tsx` | done (an ask is approved, never turned away: the backend has no route for that, and the web has none either) |
| Works with other Lemmas | `space/connect-teammate.tsx` | done |
| Skills, memory | `skills/skills-view.tsx`, `space/what-it-remembers.tsx` | done (a skill or note opens in the Files viewer; "New skill" opens a chat with the words in the box, unsent) |
| Model it runs on | `shell/runs-on.tsx` | done |
| MCP access and grants | `space/agent-access.tsx` | done |
| Delete a Lemma | `space/delete-teammate.tsx` | done (typing its name confirms) |
| Connect WhatsApp, Telegram, Email, Slack, Teams, Discord; phone verification | `shell/reach.tsx`, `own-bot.tsx`, `surface-*` | done (every way in the web offers; Discord is named but the backend has no Discord platform yet; no real third-party account was connected, see below) |
| Profile: name, time zone, mobile | `session/profile-panel.tsx` | done (WhatsApp verification; Telegram verification needs a browser cookie, so the phone sends that one to the web) |
| Appearance: light, dark, as the phone | settings modal | done |
| Usage | `usage/usage-panel.tsx` | done |
| Plan and billing | `billing/*` | done (iOS: shown, never bought; see below) |
| Organization people, invitations, roles, who can join | `org/people.tsx`, `who-can-join-org.tsx` | done |
| Connectors: catalog, accounts, auth configs, rotate credentials | `org/connectors.tsx`, `connect/*` | done |
| Models and provider keys, coding agents | `org/models.tsx`, `agent-settings.tsx` | done (coding agents read only: they are set up on their own computer) |
| Organization usage, team billing | `org/org-usage.tsx`, `billing/team-billing.tsx` | done |
| Switch organization, sign out, server | `(in)/account.tsx` | done |

How it is built:
- One folder per part under `src/areas/admin/` (`about`, `people`,
  `works-with`, `skills`, `memory`, `mcp`, `reach`, `account`, `billing`,
  `org`, `models`, `connectors`), each with its own source, live, sample and
  hooks, under one key of `AdminSource` each. `access/` asks the server who
  may do what (`podPermissions.me`, the members list) so a screen offers only
  what the server allows. `ui.tsx` is the settings kit every screen is drawn
  with. The web's pure modules are imported where they live (`@web/…`).
- Signing in to a provider (connectors, Slack, Teams) opens the provider in
  the phone's browser as an auth session (`connect/round-trip.ts`), never a
  WebView. The backend only lets the callback end on a path of the web app,
  so the person closes the browser to come back, and the app reads the
  accounts again to find the one that sign-in made. The return path names
  `client=mobile`; once the web's `/oauth/complete` page sends such a round
  trip to `lemma://connect/done` (as its sign-in page does), the browser
  closes by itself: the app already waits for that address and keeps it out
  of the router (`+native-intent.tsx`).
- **Billing on iOS, pending a product decision.** Apple restricts linking out to
  buy a subscription inside an iOS app. On iOS the plan, the plans on offer,
  the seats and the invoices are shown, but no purchase, upgrade, checkout,
  payment or stop-renewing control is drawn; one quiet sentence names the
  server's web address instead ("Change your plan on the web, at lemma.work.",
  `admin/billing/words.ts` `buyingHere`). Whether iOS should list the other
  plans and their prices at all is part of that call. Android buys in the
  phone's browser and reads the plan again on return.
- A local installation has no billing: Plan and Billing are not listed there.
- No real third-party account was connected while this was built. Each
  platform's flow runs on the phone up to the point that needs one: a real
  Meta number (WhatsApp of its own, groups, verification), a BotFather token
  or the manager bot (Telegram), a Resend key and inbound domain (email), a
  Slack workspace, configuration token or app, a Microsoft 365 tenant and an
  administrator's approval (Teams), a GitHub App install, a real MCP server,
  OpenAPI spec or database.
- Not on a phone, as on the web outside the desktop app: "This Mac", setting
  up a coding agent on this computer, local model servers.
- Sample data: Acme, with Ada as its owner; every part has its own
  (`<part>/sample.ts`), and writes stay in memory while the app is open.

## G. Everything else (`src/areas/more`, `(in)/search`, `(in)/new-lemma`, `welcome`, `new-org`, `invitations`, `join`, `templates`, `import`, `remix`, `computer`, `sign-in-to-site`, `call`, `guide`)

A part per key in `MoreSource` (`search`, `welcome`, `begin`, `install`,
`share`, `computer`, `call`, `tour`), each in its own folder with its own
source, live and sample side and hooks. Where the web has a page, the
phone's screen sits at the same path (`invitations/{id}/accept`,
`import/github/{owner}/{repo}`, `remix`, `templates`,
`sign-in-to-site/{conversation}/{call}`), so a `lemma://` link in the web's
grammar opens it; `+native-intent.tsx` drops the host from the web's own
https address and keeps a link that came while signed out until the person
is in (`areas/more/links.ts`, `link-return.tsx`).

| Feature | Web | Status |
|---|---|---|
| Search across files, tables, agents, workflows, apps, schedules, people | `search/palette.tsx`, `use-search.ts` | done (every Lemma, or one; Ask {Lemma} first; rows bounded across the whole search; each source that fails is named; every hit opens the phone's own screen) |
| First run: invitations, suggested organizations, join, new organization | `org/arrival-view.tsx`, `new-org-dialog.tsx` | done (on Home for somebody with no organization; Account › Invitations and organizations to join, and New organization, for everyone else) |
| First profile step | `session/first-profile-step.tsx` | done (a sheet: name, time zone, the phone by WhatsApp; then the tour, never both at once) |
| Start a Lemma | `stage/hiring.tsx`, `/o` new-Lemma list | done (what it should own, your own sentence, only a name, or a template; the steps ticked as they finish; a missing model said) |
| Ask to join a Lemma | `shell/not-yours.tsx` | done (a Lemma's page and `join/{id}`; one of yours in another organization opens there) |
| Invitation links, accept or decline | `site/invitation.tsx` | done (decline is refused by the server for the person invited, as on the web; the phone says so) |
| Templates, import, remix | `site/import/install-panel.tsx`, `/templates`, `/import/github`, `/remix` | done (the journey in a tall sheet over the repository's page; an install left behind is offered back) |
| Share a Lemma, file, table or app | `space/share-sheet.tsx` | done (one sheet; a file's public links open area C's sheet over it) |
| The agent's computer: status, live browser, files, saved logins | `computer/*` | done (one computer per person; the live screen over a socket the app holds, full screen with Keyboard and Paste; sign-in requests at the web's path) |
| Voice call | `call/*` | done, on the call socket the backend holds (#1025): the microphone and speaker in a WebView, the socket in React Native; offered only where the server names a voice |
| Product tour | `tour/*` | done (over Home, offered once in the first week; Help on the account screen has it again, and how each place works) |

How it is built:
- **The web's own code.** Each part imports the web's pure modules where
  they live (`@web/…`, `web-imports.js`): search's matching and sources,
  arrival and first-profile, the hiring catalogue, the import plan, types,
  catalogue, pending install and README reading, the computer's machine,
  startup, live, logins and sign-in, and twelve call modules. The phone's
  reading on top is each part's `model.ts`.
- **Search.** The web searches one pod; the phone has no "current Lemma"
  where search opens, so it searches every Lemma, with scope pills. Two
  departures, on purpose: a file found by its contents and a row found by
  another column stay in the list, below title matches, and a table whose
  row query fails is named.
- **The computer's live screen.** React Native opens the browser-view
  socket with the session in an `Authorization` header (the backend reads
  the header first, and allows a native client's origin) and relays its
  frames, as base64, to noVNC in a WebView (`webview/src/vnc`,
  `npm run build -- vnc`), which only draws; no token ever reaches the
  page. The far display is fitted to the box, so a site serves its phone
  layout. The page draws its own Keyboard / Paste / Done bar, because an
  Android WebView opens the soft keyboard only for a tap inside the page.
- **The call.** A call is a conversation the backend holds (#1025): `calls.open`
  makes it, and one socket (the web's `openCallSocket`, here with the token
  in a header too) carries 16 kHz PCM up, the voice down at the rate `ready`
  names, and captions, work and canvas items as JSON. The socket is React
  Native's; the microphone and speaker are the web's `pcm-audio.ts` in an
  invisible WebView (`webview/src/call`, loaded on a secure address that
  reaches nothing), and PCM crosses the bridge as base64, the voice gathered
  for 60 ms a trip. What the Lemma shows is drawn from the chat that drew
  it, and paging to it tells the call. The voice is briefed with the Lemma
  (`pod-brief.ts`) and the chat the call grew out of (`history-brief.ts`).
  Afterwards the call's own chat holds what was asked and done. The mic is
  offered only where `calls.config` names a voice. app.json declares
  `RECORD_AUDIO` and the iOS microphone sentence.
- **The tour.** Controls register with `TourTarget` (Home's headline and
  cards, its ask box, search and profile buttons, the tab bar's Lemmas and
  Apps); each stop lights the real control through an even-odd scrim and
  puts the card along the edge the control is not near. `HomeOverlays`
  shows the first profile step, then the tour, never together, and neither
  on the visit an install opens.
- **Shared files touched** (each said in its commit): Home, the tab bar, a
  Lemma's page (Share, not one of yours), the account screen (your
  computer, organizations, Help), `+native-intent.tsx`, `(in)/_layout.tsx`,
  `ui/ask-box.tsx` and `thread/composer.tsx` (the mic starts a call),
  `ui/framed.tsx` (a header slot), area A's sign-in card, area B's table
  screen and area C's file screen and Files menu (Share), `webview/build.mjs`
  (two more pages), app.json.

Checked on the emulator against the local stack (light) and in sample data
(dark), with what the local stack could not show:
- Search: rows, a file found by its contents, a chat; a row opens its page.
- The computer: woken, files, signed-in sites, the live browser inline and
  full screen, typing an address with the page's keyboard (the far site
  loaded and served its phone layout).
- First run: the local test account's email is unverified, and the backend
  lists invitations only for a verified address, so the arrival list is
  empty there by design; invitation links work by id and were accepted
  (landing on its Lemma in its organization) and declined (refused, said).
  Ask to join: a closed Lemma's ask waits and opens once the host lets you
  in; an open one lets you in at once.
- Starting a Lemma: made in its organization, its setup chat opened.
- Installing: Choose, Review (the real plan from GitHub) and Install ran;
  neither template finished on the local stack, for backend reasons the
  phone shows in the server's words (Drop's event automation fails
  validation: `ScheduleCreateEntity` wants `table_name`, the bundle names
  `datastore`; Lemma GTM's app build step fails on this stack). Finishing is
  checked in sample data.
- In sample data, dark: every screen above, from the first profile step and
  search to the tour started from Help. That pass found
  and fixed five things: a second join or invitation link kept the first's
  answer; the tour, started from Help over another screen, lit Home's
  controls through it; Arrival offered an invitation to the organization you
  were already in; a remix of a GitHub repository was named "Github"; and a
  public app's share sheet said both "anyone" and "people in it" about one
  link.

Not done here:
- iOS was not run (the Android emulator only).
- The web's https links (`lemma.work`) open in the app only
  once the web serves `assetlinks.json` (and, for iOS, an
  `apple-app-site-association` with associated domains). Until then they open
  in the browser; `lemma://` links open the app now.
- Finishing an install against a real server waits on the two backend
  failures above.
- Sample data: Acme, with Ada; each part has its own `sample.ts`. The
  invitations, Lemmas to join (`sample-closed`, `sample-waiting`,
  `sample-open-door`, `sample-missing`), the computer and its sign-in request
  (`sign-in-to-site/launch-press/call-s2`) each have an id for every state.
