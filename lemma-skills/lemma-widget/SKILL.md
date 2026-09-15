---
name: lemma-widget
description: "Create lightweight inline Lemma widgets for conversations via display_resource(type=\"WIDGET\"): self-contained HTML/CSS/JS for metrics, lists, comparisons, timelines, record details, previews, and charts, optionally powered by live pod data through the browser Lemma SDK. Use an app, not a widget, when the UI needs React, routing, or substantial application state."
---

# Lemma Widget

A widget is the default way to **show an answer that is more than short prose**.
Use `display_resource(type="WIDGET")` whenever the useful result has structure or
visual hierarchy: several values, records, statuses, steps, comparisons, a timeline,
a compact table, a preview, or a chart.

A widget is also where an answer *continues*: it reads live pod data, it can be
filtered and opened and sorted in place, and it can offer the person their next
question ready to send.

Use plain text only for a single fact, a short explanation, or narration around the
widget. If an existing FILE, TABLE, APP, or other pod resource already represents the
answer, display that resource directly instead of recreating it as a widget.

## Widget or app?

- **Widget:** one compact inline view; plain HTML/CSS/JS; quick to render in
  the conversation; little local state.
- **Vite app:** React, routing, multiple screens, reusable components, substantial
  interaction/state, or a UI people will return to as a product.

React, ReactDOM, Tailwind, and the agent web-component bundle belong in a Vite
app — Lemma has full app support for that class of UI. A widget stays lightweight,
and can be saved as an HTML app later.

Widgets are display surfaces. `ask_user` collects fixed choices; prose collects
free-form input.

## Build one

1. If the widget uses pod data, inspect the real table and column names first:

   ```bash
   lemma tables list
   lemma tables get <table>          # exact columns; `pods describe` folds them
   lemma query run "select * from <table> limit 5"
   ```

2. Load the closest maintained starter with `load_skill`, using
   `name="lemma-widget"` and one of these `resource_path` values:

   | Answer shape | Starter |
   | --- | --- |
   | Metrics grouped by one field | `assets/widget-starter-v1.html` |
   | Compact record list | `assets/widget-list-v1.html` |
   | Bar chart grouped by one field | `assets/widget-chart-v1.html` |
   | One record with selected fields | `assets/widget-detail-v1.html` |

3. Replace every uppercase `__PLACEHOLDER__` with inspected names and useful labels.
   For `__FIELD_CONFIG__`, insert a JSON array such as
   `[{"label":"Owner","field":"owner"}]`. In the metric and chart starters
   `__TABLE_NAME__` and `__GROUP_FIELD__` land inside a SQL statement, so they take
   the exact table and column identifier — not a display label.

4. Adapt the content and styling, then call `display_resource` with `type="WIDGET"`.
   The starter's SDK loader and loading/empty/error scaffolding carry over as-is.

The backend rejects unresolved placeholders, broken SDK loaders, and malformed
markup before display.

## The `display_resource` call

`type="WIDGET"` takes **exactly one** of:

- `content` — your inline HTML fragment (the usual case), or
- `public_url` — a URL to embed instead.

Passing both, or neither, is rejected. One more WIDGET-only field:

- `loading_messages` — up to **4** short lines shown while the widget renders.
  Setting them on any other resource type is rejected.

`name`, `path`, `filters`, and `query` belong to other types.

### One call, one argument

`content` is written once, in the tool call, and the widget is shown the moment
that call succeeds. Nothing stages a widget: there is no second call that
completes a first, no path or handle that points at markup you wrote into the
workspace, and no draft that is not yet in front of the person. So:

- **Write the fragment out in full, in the argument.** Building it in a
  workspace file first is a fine way to check your own markup. It is not a way
  to hand it over — the file is yours, and `path` is for FILE.
- **Never put a sentence in `content`.** Not narration, not "clean version
  below", not a note about the markup you are about to write, not a placeholder
  standing in for it. Content with no tag in it is rejected, and text in front
  of the first tag ships as a bare unstyled line above the view. What the person
  should read goes in your reply.
- **Never display a probe.** A call that succeeds is shown, so "testing whether
  this transmits" is a test run in front of the person. Check your own markup
  against the list under [Before display](#before-display) instead.
- **If the fragment is genuinely too long to write out, it is an app.** Save the
  HTML as an app and pass its address as `public_url`.

The starters are a shape to follow, not a file to transcribe. Take the SDK
loader and the loading/empty/error scaffolding verbatim, and write the markup
your answer actually needs around them.

## Fixed contract

- `content` is an HTML **fragment**: raw markup, body-level tags only. A doctype,
  `<html>`, `<head>`, `<body>`, or an encoded blob is rejected before display.
- The markup parses as what it reads. A tag that lost its `<` becomes a text node:
  the element never exists, everything it styled renders plain, and the tag shows
  up as text. That, and a close tag with nothing open, is rejected.
- A standalone SVG image is a pod file: `lemma files upload`, then
  `display_resource(type="FILE", path=...)`. Inline `<svg>` icons *inside* an HTML
  fragment are part of the fragment.
- All CSS is local. The widget runs in its own iframe and inherits no frontend CSS.
- JavaScript is plain browser JS — no build step, JSX, React, or framework runtime.
- Secrets, credentials, pod ids, and environment hostnames stay out of the HTML.
- Loading, empty, error, and narrow-screen states are all deliberate.
- Values reach the DOM through `textContent`, or escaped before `innerHTML`.
- The view stays compact: no fixed positioning, no nested scrolling.
- **Height is capped.** The inline view clips at **480px** with a fade and an
  Expand control, and a self-reported height above 2400px is ignored. Design for
  the fold: put the answer at the top, not below a long table.
- A widget **offers** a message; it never sends one. `composeInConversation`
  puts text in the conversation's composer for the person to send, edit, or
  ignore — see [Let it answer back](#let-it-answer-back). Everything else the
  frame might want to say to the host is not part of the contract.
- Use `ask_user` when the run cannot continue without an answer. A widget's
  offer arrives after the run is over; `ask_user` pauses it.

The starters are platform-themed and system-aware: their
`prefers-color-scheme: dark` rules and semantic fallbacks carry over intact. They
consume the public token layer — `--lemma-widget-bg`, `surface`, `subtle`, `text`,
`muted`, `border`, `accent`, `danger`, `danger-soft`, `radius`, `font`, and
`color-scheme` (each with the full `--lemma-widget-` prefix). Chart starters also
expose `chart-1` through `chart-5`.

That list is **exhaustive** — the host injects only these. The frontend posts a
larger palette (`success`, `warning`, `info`, `accent-hover`, …), and anything
outside the published set is filtered out before it reaches your iframe, so
`--lemma-widget-success` resolves to nothing. Frontend variables such as
`--text-primary` never cross the iframe boundary either.

**Every token reference carries a fallback** — `var(--lemma-widget-surface, #fff)`,
not `var(--lemma-widget-surface)`. The host delivers the palette by `postMessage`,
so it arrives after first paint, and it never arrives at all when the widget is
opened outside the conversation frame. A bare reference resolves to nothing there
and the widget renders colorless.

## Loading the SDK

For a data-backed widget, preserve the starter's browser SDK loader:

- Build the SDK URL from `window.__LEMMA_CONFIG__.apiUrl`.
- Load `/public/sdk/lemma-client.js` dynamically and start in `sdk.onload`.
- Construct `new window.LemmaClient.LemmaClient()` with no arguments.
- Call `client.initialize()` and handle a non-authenticated state.
- SDK calls run as the signed-in user under normal RLS and grants.
- **`unauthenticated` does not mean "signed out."** The widget is a cross-site
  iframe and the browser SDK authenticates by cookie, which some browsers and
  local HTTP setups withhold. So write that branch as "this view can't load your
  data here", not "sign in to Lemma" — the user usually *is* signed in. Keep the
  widget useful without data where you can.
- Shared files use `/…`, personal files use `/me`. There is no `/pod/...` prefix.

Common calls:

```js
await client.records.list("tickets", { limit: 50 });
await client.records.get("tickets", "record-id");
await client.datastore.query(
  "select status, count(*) as matched from tickets group by status"
);
await client.files.search("quarterly planning", { limit: 10 });
await client.files.children.markdown("/knowledge/report.pdf");
await client.files.children.content(
  "/knowledge/report.pdf/pages/page_0001.jpg"
);
```

Both `children` calls resolve to a `Blob`; call `.text()` on the markdown one
before rendering it.

A widget that stays live opens the change stream rather than polling with
`setInterval`:

```js
const handle = client.datastore.watchChanges({
  table: "tickets",              // options object is required
  onChange: (f) => { /* f.operation, f.record_id, f.payload */ },
});
// later: handle.close();
```

It is a WebSocket with its own auth, so it is subject to the same cross-site
constraint as `initialize()` above — always keep the non-live render working.

## Make it do something

A widget is a small program with a live connection to the pod, not a picture of
an answer. Reach for that whenever the answer has more in it than fits on screen
at once:

- **Narrow it in place.** A status filter, a date range, a search box, a sort —
  re-query in `onchange` rather than rendering every row and hoping.
- **Open a row.** A list where clicking a record swaps the panel for its detail
  is one `records.get`, and it saves the person a round trip through you.
- **Keep it current.** `datastore.watchChanges` for a view someone leaves open.
- **Show the thing itself.** `files.children.markdown` and `.content` render a
  document's own pages inside the widget instead of describing them.

What stays out: anything needing React, routing, or state worth persisting —
that is an app. And anything destructive. A widget may read, filter and offer;
a write behind a button in a view somebody clicked without reading is not a
decision anyone made.

## Let it answer back

`composeInConversation(text, options?)` asks the host to put `text` in the
conversation's composer. It is on the browser SDK's global, beside the client:

```js
const { composeInConversation, canComposeInConversation } = window.LemmaClient;

if (canComposeInConversation()) {
  button.onclick = () => composeInConversation(
    "Why is " + account.name + " cooling?",
  );
}
```

Three things about it, and all three matter:

- **It fills the box; it does not send.** The person reads what arrived, edits
  it or not, and presses enter. So write the text as *them* asking — "Why is
  Acme cooling?", not "The user would like to know about Acme."
- **It can be unavailable.** It resolves `false` when nothing is hosting the
  widget — an app opened from a share link has no conversation anywhere near
  it. Check `canComposeInConversation()` before drawing the button, and let the
  widget be useful without it. A button that quietly does nothing is worse than
  one that was never there.
- **`{ newConversation: true }` is for a handoff.** The default lands in the
  thread the person is looking at, which is what "ask about this" means. Pass
  the flag when the point is that the subject gets a thread of its own rather
  than landing in the middle of something else.

Two or three offers on a widget is plenty, and each should be a question the
person would plausibly ask next. A row of eight buttons is a menu, and a menu is
an app.

## Never count what came back

Both read paths are capped, and the two report it differently. Getting this wrong
prints a number that is simply false in front of the person who asked for it.

- `records.list` serves **one page, at most 1000 rows** — a `limit` above that is
  rejected, not clamped. Its `total` *is* the exact RLS-scoped count of matching
  rows, so `items.length` is what you are showing and `total` is what exists.
  Say "12 of 480" rather than letting twelve stand for all of it.
- `datastore.query` returns `{ items, total, truncated }`, and here `total`
  counts **rows returned, not rows matched**. When `truncated` is true the row
  cap cut the result short and `items` is a prefix of the real answer, so the
  count is a floor. Narrow the query rather than label a floor as a total.

Aggregate in SQL. A count taken over a page of records is a count of the page:
group in the database (`select status, count(*) …`) and a widget over a 5,000-row
table stops reporting the first hundred rows as the whole table. The metric and
chart starters do exactly this; keep their query rather than counting rows in JS.

## Visual standard

- Lead with the answer, not a title-heavy dashboard shell.
- Follow Lemma's neutral surfaces, near-black/near-white text, indigo action color,
  restrained borders, and compact radii unless the content needs a distinct visual
  language.
- For a very small widget, the token-light set is just `surface`, `text`, `muted`,
  `border`, and `accent`.
- Format numbers and dates for humans.
- Use a list for repeated records, cards for a handful of metrics, a detail layout
  for one record, and a chart only when shape or comparison matters.
- For Chart.js, give the canvas wrapper an explicit height and read chart/text/grid
  colors from the starter's semantic variables.
- Keep explanation outside the widget in the assistant response.

## Before display

- The chosen view is genuinely more useful than short prose.
- `content` opens with `<` and carries the entire fragment, not a note about it.
- The closest versioned starter was used and all placeholders were replaced.
- Every tag opens with `<` and closes once; the fragment carries no full-document
  tags, secrets, hardcoded hosts, or pod ids.
- Every `--lemma-widget-*` reference has a fallback value.
- SDK code uses injected config and boots from the script load handler.
- No displayed count is the size of a page: totals come from SQL or from
  `records.list`'s `total`, and a `truncated` query result says so.
- Loading, empty, error, and mobile states are present, and the
  non-authenticated branch does not tell a signed-in person to sign in.
- Anything the person can click does something here, or offers something to the
  composer. Nothing writes, and nothing claims to have sent a message.
- Every compose button is behind `canComposeInConversation()`, and its text
  reads as the person's own words.

For React or a full product UI, load `lemma-builder` and follow
`references/apps.md`. For interaction-tool behavior, see
`lemma-builder/references/agent-tools.md`.
