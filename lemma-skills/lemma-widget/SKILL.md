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

2. Take two things verbatim, the way you would take a library: the token
   preamble `assets/widget-tokens-v1.css`, pasted at the top of your `<style>`,
   and — if the widget reads pod data — the SDK loader from any example. Then
   draw what the answer needs around them.

   The preamble is the design language in one block: the full `--lemma-widget-*`
   vocabulary, every reference carrying a fallback, light and dark. Take it and
   a widget you invent from nothing still looks like it belongs. Skip it and you
   will hand-roll twenty lines of `var(--lemma-widget-surface, #fff)` and get one
   of them wrong.

3. Read the example nearest your answer with `load_skill`, using
   `name="lemma-widget"` and one of these `resource_path` values. They are a
   range to learn the language from, **not a table to pick from and transcribe**:

   | The answer you have | Example |
   | --- | --- |
   | A judgement, with a distribution behind it | `assets/widget-finding-v1.html` |
   | Records you want read down, and narrowed | `assets/widget-table-v1.html` |
   | One record, and what happened to it | `assets/widget-record-v1.html` |
   | One measure over time | `assets/widget-trend-v1.html` |
   | One measure across categories | `assets/widget-ranked-v1.html` |
   | Something you already know — no pod data | `assets/widget-note-v1.html` |

   Replace every uppercase `__PLACEHOLDER__` — **except `__LEMMA_CONFIG__`,
   which must stay exactly as it is.** That one is not a template slot: it is the
   runtime config the host injects, and the SDK loader reads it. Validation
   rejects a widget that stops reading it. The slots you do fill are the data,
   label and judgement ones: `__TABLE_NAME__`, `__RECORD_ID__`, `__GROUP_FIELD__`,
   `__STATUS_FIELD__`, `__TITLE_FIELD__`, `__SUBTITLE_FIELD__`, `__DATE_FIELD__`,
   `__FIELD_CONFIG__`, `__TIMELINE_CONFIG__`, `__WIDGET_TITLE__`,
   `__FOCUS_VALUE__`, `__FOCUS_CLAIM__`, `__COMPOSE_TEXT__`, `__EMPTY_LABEL__`.
   For `__FIELD_CONFIG__`, insert a JSON array such as
   `[{"label":"Owner","field":"owner"}]`. Anything landing inside a SQL statement
   takes the exact table or column identifier — not a display label.

   The claim slots are yours, not the data's: `__FOCUS_CLAIM__` is the sentence a
   person would say about the number, and no query can write it. A widget that
   says "22 tickets" where it could say "22 tickets are blocked — the most since
   June" has made the reader do the work they asked you to do.

4. Write the fragment to a pod file with `pod_write_file` — `/me/c/<date>/<name>.html`
   alongside the rest of this conversation's work — then display that path:

   ```
   display_resource(type="WIDGET", path="/me/c/2026-09-15/pulse.html")
   ```

   The preamble, the SDK loader and the loading/empty/error scaffolding carry over
   as-is.

The backend rejects unresolved placeholders, broken SDK loaders, and malformed
markup before display.

## The `display_resource` call

`type="WIDGET"` takes **exactly one** of:

- `path` — a pod file holding the widget's HTML. **The one to reach for.**
- `content` — the HTML inline, for something small you will not revisit.
- `public_url` — a URL to embed instead.

Passing more than one, or none, is rejected. One more WIDGET-only field:

- `loading_messages` — up to **4** short lines shown while the widget renders.
  Setting them on any other resource type is rejected.

`name`, `filters`, and `query` belong to other types.

### Put it in a file

A widget's HTML is a file you write, the way everything else you build is. That
is not a storage detail — it is the difference between work you can go back to
and work you get one shot at:

- **You can read back what you wrote** instead of trusting that a few thousand
  characters came out of a single tool argument intact. One missing `<` has
  shipped a widget with a stray bracket over the top of it.
- **You can fix it by editing it.** The served widget is whatever the file says
  *now*, so correcting one is an edit to a few lines, not a retype of the whole
  fragment — and a retype is where a working widget picks up a new bug.
- **You can check it before anyone sees it.** The file exists before the
  display does. Read it, run it past [Before display](#before-display), and only
  then call.

So: `pod_write_file` to `/me/c/<date>/<name>.html`, then
`display_resource(type="WIDGET", path=...)`. A pod path, never a workspace one —
the widget is read as the person looking at it, and they are not in your sandbox.

`content` still takes an inline fragment, for something small you are sure of.
It is frozen in the tool call the moment it succeeds: no editing it, no taking
it back, and a second call is a second widget that lands *underneath* the first
with the mistake still showing above it. If you are inlining, you get one look.

Either way:

- **Never put a sentence in the HTML's first line.** Not narration, not "clean
  version below", not a note about the markup. Content with no tag is rejected,
  and anything before the first tag renders as a bare unstyled line above the
  view. What the person should read goes in your reply.
- **Never display a probe.** A call that succeeds is shown. "Testing whether
  this transmits" is a test run in front of the person — and with a file there
  is finally somewhere to test that is not their screen.
- **A widget people will come back to is an app.** Save the HTML as an app and
  pass its address as `public_url`.

The examples are a shape to follow, not a file to transcribe. Take the SDK
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
  Expand control, and a self-reported height is clamped to 2400px. Design for
  the fold: put the answer at the top, not below a long table. The frame follows
  the content both ways — a view that narrows itself shrinks the frame with it.
- A widget **offers** a message; it never sends one. `composeInConversation`
  puts text in the conversation's composer for the person to send, edit, or
  ignore — see [Let it answer back](#let-it-answer-back). Everything else the
  frame might want to say to the host is not part of the contract.
- Use `ask_user` when the run cannot continue without an answer. A widget's
  offer arrives after the run is over; `ask_user` pauses it.

The examples are themed and system-aware: their `prefers-color-scheme: dark`
rules and semantic fallbacks carry over intact, and all of it lives in the one
preamble they share.

The host sends **the whole `--lemma-widget-*` vocabulary** — it is taken by
prefix, not from a list of approved names, so a token a host publishes reaches
you. Ground and surface (`bg`, `surface`, `subtle`, `raised`), three weights of
ink (`text`, `muted`, `faint`), rules (`border`, `border-strong`), the accent and
**the ink that goes on it** (`accent`, `on-accent`, `accent-soft`, `accent-line`),
state and its inks (`success`/`on-success`, `warning`, `danger`/`on-danger`,
`danger-soft`, `info`), `chart-1` through `chart-5`, the corner scale (`radius-sm`,
`radius-md`, `radius`, `radius-panel`), `shadow-rest` and `shadow-raise`, `font`
and `font-mono`, and `color-scheme`.

A host that has no answer for a name leaves it out rather than guessing, which is
the entire reason for the next rule.

**Every token reference carries a fallback** — `var(--lemma-widget-surface, #fff)`,
not `var(--lemma-widget-surface)`. The host delivers the palette by `postMessage`,
so it arrives after first paint, and it never arrives at all when the widget is
opened outside the conversation frame. A bare reference resolves to nothing there
and the widget renders colorless.

## Loading the SDK

For a data-backed widget, preserve the examples' browser SDK loader:

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

**Read the keys your own query produces.** `sum(runs_failed) as failed` returns
`failed`, and `row.runs_failed` on that result is `undefined`. Nothing throws:
`Number(undefined) || 0` is `0`, and the widget renders a confident, plausible,
wrong number — "none failed" over a window full of failures. Nothing in the
platform can catch this for you, because that column may be a real key of a
*different* query in the same widget. Alias deliberately, and read back the name
you aliased to.

Aggregate in SQL. A count taken over a page of records is a count of the page:
group in the database (`select status, count(*) …`) and a widget over a 5,000-row
table stops reporting the first hundred rows as the whole table. The metric and
chart examples do exactly this; keep their query rather than counting rows in JS.

## Visual standard

A widget is drawn inside a conversation, which is the one surface in this app
allowed contrast: a message has to sit on something and a card has to read as a
card. So the view gets figure and ground — and then these, which are what
separate a view from a dashboard shell with your data poured into it.

**Shape the view to the answer, not to the query.** This is the one that matters.
`select x, count(*) group by x` has a shape, and it is not an answer's shape.
Bent into a template it produces a count with no denominator, four cards that
say nothing is more important than anything else, and a chart of a field nobody
asked about. Start from the sentence you would say out loud, and build the view
that supports it.

- **Lead with the finding.** A sentence with a number in it, not a number with a
  label under it. The person asked a question; answer it in the first line.
- **Every figure carries a denominator or a direction.** `22` is trivia. `22 of
  480, up nine this week, none moved in nine days` is an answer. A count with
  neither is a number you have made somebody else interpret.
- **Hairlines, not boxes.** Ruled rows inside one card, not twelve bordered pills
  each competing to be looked at. Shadow carries weight — `shadow-rest` — so a
  card is an object on the page rather than a rectangle with a line around it.
- **Status owns a colour; the accent means "you can act here".** `success`,
  `warning` and `danger` are reserved for state and never for "series four". The
  accent appears about four times in a view, not forty. Every fill of it is
  written in `on-accent` — never white, which measures 1.2:1 on a light accent
  in dark mode.
- **Figures are mono and tabular** (`font-mono`, `font-variant-numeric:
  tabular-nums`). Numbers that do not align cannot be compared, which is the only
  reason to put them in a column.
- **Weight never exceeds 500.** Hierarchy comes from size and the space above
  something. A 700 is not emphasis, it is shouting in a quiet room.
- **The ground is paper, not screen.** Warm stock, not `#ffffff` on `#f5f5f5`.
- **End with the next question**, in the person's own words. See
  [Let it answer back](#let-it-answer-back).

### Charts

- **Pick the form from the data's job.** Change over time → a line. Magnitude
  across categories → horizontal bars, so the labels read straight instead of
  rotated under a column. One number that matters → not a chart at all; say it.
- **Draw it yourself in SVG.** `widget-trend-v1` and `widget-ranked-v1` load
  nothing: a widget has 480px of fold and has to paint now, and a chart library
  from a CDN is a 200KB download and a second failure mode inside an iframe that
  already has one. Reach for a library when you need dense multi-series or a real
  axis — and then give the canvas an explicit height.
- **Never a dual axis.** Two measures of different scale are two charts.
- **`chart-1` through `chart-5`, in order, never cycled.** They are a measured
  set: adjacent pairs clear ΔE 8 under deuteranopia and ΔE 15 under normal
  vision. A sixth category is not a sixth colour — fold the tail into one "Other"
  in *ink*, not in a categorical hue, which is what `widget-ranked-v1` does.
- **One series needs no legend** — the title names it. Two or more always get
  one, because colour alone is not an identity channel.
- **Text never wears the data colour.** Bars and lines carry the hue; labels,
  values and axes stay in `text` / `muted` / `faint`. A coloured dot beside a
  label carries identity; a coloured label is just hard to read.
- **Label selectively.** The endpoint, the extreme, or the one series the story
  is about. A number on every point is the axis again, in a worse typeface.
- **Recessive grid**: hairline, solid, one step off the surface. Never dashed.
- **Ship the hover layer and the table.** A chart in a browser that does not
  respond to a pointer is a screenshot; a chart whose numbers exist only as
  pixels is unreadable to anyone not looking at it. Both examples carry a
  visually-hidden table — keep it.

Format numbers and dates for humans. Keep explanation outside the widget, in
your reply.

## Before display

- The chosen view is genuinely more useful than short prose.
- The HTML lives in a pod file, unless it is small and settled enough to inline.
- It opens with a tag — not a stray character, not a sentence — and is complete.
  An inline fragment gets no second look; a file can be edited afterwards.
- Every value read off a query result uses the name that query aliases it to.
- The token preamble is present, and every placeholder except `__LEMMA_CONFIG__`
  was replaced — including the claim slots, which no query can fill for you.
- Every tag opens with `<` and closes once; the fragment carries no full-document
  tags, secrets, hardcoded hosts, or pod ids.
- Every `--lemma-widget-*` reference has a fallback value.
- SDK code uses injected config and boots from the script load handler.
- No displayed count is the size of a page: totals come from SQL or from
  `records.list`'s `total`, and a `truncated` query result says so.
- Loading, empty, error, and mobile states are present, and the
  non-authenticated branch does not tell a signed-in person to sign in.
- The view is shaped by the answer, not by the query behind it: the first line
  is the finding, and every figure carries a denominator or a direction.
- Status wears state colour, the accent appears about four times, every accent
  fill is written in `on-accent`, and no weight exceeds 500.
- A chart labels selectively, keeps text in ink rather than the data colour,
  responds to a pointer, and carries its numbers as a table as well.
- Anything the person can click does something here, or offers something to the
  composer. Nothing writes, and nothing claims to have sent a message.
- Every compose button is behind `canComposeInConversation()`, and its text
  reads as the person's own words.

For React or a full product UI, load `lemma-builder` and follow
`references/apps.md`. For interaction-tool behavior, see
`lemma-builder/references/agent-tools.md`.
