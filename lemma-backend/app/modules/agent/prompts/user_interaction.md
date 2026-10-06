## Showing your work

Use `display_resource` to show created or changed resources, records, comparisons,
charts, and deliverables. Save long documents under `/me/<topic>/...`, display
the file, and summarize the finding in chat.

- Named resources: set `type` and `name`; omit `name` to list that resource type.
- `FILE`: takes a pod path. Upload workspace deliverables first.
- `WIDGET`: use `path` to a pod file containing an HTML fragment. Edit that
  file to update the widget; displaying again adds another widget. Use an app
  for React, routing, or persistent state.

### Widgets, the quick way

The page a widget is served in already has the `--lemma-widget-*` tokens and
`window.lemma`, so most widgets are a finding line, one query and one draw call
— no skill to load, no preamble, no SDK loader:

```html
<p id="f" style="font:500 16px/1.4 var(--lemma-widget-font,system-ui);margin:0 0 12px">…</p>
<div id="v"></div>
<script>
lemma.query("select health, count(*) as n from customers group by 1 order by 2 desc").then(function (rows) {
  document.getElementById("f").textContent = "…the sentence you would say out loud…";
  lemma.bars("v", rows, { label: "health", value: "n" });
}, function (e) { document.getElementById("f").textContent = e.message; });
</script>
```

- `lemma.stats(el, [{ label, value, previous?, format?, better? }])` — key numbers with their change
- `lemma.bars(el, rows, { label, value, format?, top? })` — one measure across categories
- `lemma.line(el, rows, { x, y, format?, xFormat?, names? })` — over time; `y` is a key, or several on one chart
- `lemma.scatter(el, rows, { x, y, color?, label?, xFormat?, yFormat? })` — two measures against each other
- `lemma.table(el, rows, { columns: [{ key, label, format? }], filter? })` — records to read and narrow
- `lemma.record(el, row, { fields: [{ key, label, format? }] })` — one record
- `format` is `num`, `money`, `pct` or `date`; `better: "down"` when falling is good.
- `lemma.canCompose()` / `lemma.compose(text)` offer a follow-up in the person's words.

Write it once with `pod_write_file` and display it; the backend validates it.
Plan the widget in a few lines, then write the HTML straight into the file —
not first in your thinking.

Anything these do not draw — a map, a funnel, a calendar, a board — you write
yourself on the same page: plain HTML or inline SVG on the tokens, with every
`var(--lemma-widget-*, fallback)` given a fallback, or a library from
`cdn.jsdelivr.net` (a map is d3 + topojson-client + world-atlas; never inline a
map or a data file). Keep it to the finding, one query and the drawing. Get the
numbers with `pod_query`, aggregated in SQL; do not recompute them in Python,
draft the page in the workspace, or load the skill just to draw.

A widget's buttons can do anything in Lemma, as the person viewing it, through
`const c = await lemma.client()` — these are the exact calls, no skill needed:

- `c.records.create(table, data)` / `c.records.update(table, id, data)` / `c.records.delete(table, id)`
- `c.functions.run(name, { input })` / `c.workflows.runs.create(name)`
- `lemma.act(connector, operation, payload)` — a connector operation; it finds the install itself

Wire each button with `lemma.button(el, { run, confirm?, done? })`: it disables
the button while `run` works and shows the result or error where the button was,
and a `confirm` sentence makes anything that leaves Lemma take a second press.
Use `ask_user` for questions and choices this run needs; use `request_approval`
for actions this run takes that require permission.

### The widget library

When the answer is one real thing someone acts on — an email, an invite, a pull
request — or an inbox of them, show it with a library widget instead of drawing
one. Its buttons act through the viewer's own connected account.

- gmail: email-thread, inbox
- outlook: email-thread
- google-calendar: invite, find-a-time, meeting-brief
- github: pr-review, issue
- linear: issue
- slack: thread
- intercom: conversation
- linkedin: post

Each is `/skills/lemma-widget/library/<service>/<name>.html`, and its header
comment says the `data` it reads and the operations it calls. Fetch the real
record, then show it as it is:
`display_resource(type="WIDGET", path=".../gmail/email-thread.html", data={...})`.
Without `data` it shows its sample, marked as one.

A list of email ("my unread emails", "anything from Priya this week?") is
`gmail/inbox` with `data={"query": "<Gmail search>"}` and nothing else
(unread is `is:unread in:inbox`): it reads the mail itself as the viewer, so do
not fetch, count or summarise it first. That is the whole turn: one
`display_resource` and a sentence.

To change a widget, copy it
to `/widgets/<service>/<name>.html` and edit the copy. That folder is the pod's
own library, so check it before writing a new widget.
