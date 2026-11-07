---
name: lemma-setup
description: "Set a Lemma up to own its result: find where the work happens, connect it, bring in the last few weeks, keep new work coming in, count the number with set_what_you_own, and fill in /plan.md. Use in a Lemma's setup chat (\"Set yourself up to own this\"), when someone agrees to let a Lemma own something, or when a Lemma's number reads zero because nothing feeds its tables. Do not use to design a new pod's resources from scratch (lemma-builder) or for day-to-day work in a Lemma that is already set up (lemma-user)."
---

# Set up a Lemma

A Lemma is set up when four things are true:

1. The work it owns arrives in its tables on its own, one row per unit of work.
2. The last few weeks of that work are already there, so its number means
   something on the first day.
3. `set_what_you_own` holds the sentence, the target and a measure that reads.
4. `/plan.md` says how the work is done, who holds what, and what never happens.

Every question you ask during setup should change one of those four, or the
people, connections, skills or memory behind them. An answer that changes
nothing was a wasted question. What you find while reading the work (who is
waiting, what is late) is work to do after setup, not part of it.

## 0. Look before you ask

Read what is already here, in one turn:

- the runtime context: what it owns, and whether it has a measure yet;
- `pod_tables`, and `/plan.md` with `pod_read_file`;
- `list_skills`: a template brings a skill that says how its work is kept, so
  load it;
- `lemma connectors pod-list` and `lemma schedules list`.

A Lemma started from a template already has its tables, a measure and timed
schedules. It does not have a source: nothing fills those tables yet.

## 1. Ask where the work happens

One `ask_user`, two to four choices, the likeliest first, from what you know
about the work: "Intercom", "A shared Gmail inbox", "Slack", "Nowhere yet:
people tell you". Never assume email. If they pick "nowhere yet", skip to
step 5 and say rows will come from people telling you, or from a form
(`lemma-form`).

Put everything else you need into the same `ask_user` (their timezone, who
holds the work), so the person answers once. Each option is one concrete
value: "London", not "Europe (London/Berlin)". Never ask whether they are
ready or may do something; say what you need ("Upload the export here") and
end your turn.

## 2. Connect it

Run a read operation through the CLI, by catalog id and what you want:

```bash
lemma connectors run intercom "list conversations" --dry-run   # the operation and its input
```

Nothing has to be added first. When it is not connected yet, it answers
`CONNECTION_REQUIRED` with a `connect_url`: give the person that link as it
is, end your turn, and run it again when they say it is done. (An agent with
the connector tools in view can use `run_connector_operation` the same way.)

When the service is not in the catalog (`lemma connectors list`), say so in
one line and ask how the work can reach you instead: an export file they
upload, an MCP server for it, another place it already lands (a Gmail inbox
it forwards to), or people telling you. Then carry on with that.

## 3. Bring in the last four weeks

Use the connected account, or the file, to get the last four weeks of the
work (the conversations, the leads, the pull requests). Page through all of
it. Save the raw items to a workspace file; do not carry hundreds of them in
your context.

Not every item is work. Sort them with the Decisions API: closed questions,
one item per call, nothing stored. Write the questions once, as a flat JSON
Schema, and loop over the items in a shell script:

```bash
lemma decisions run --priority background \
  -i "Is this a customer asking us for something? Spam, newsletters and our own replies are not." \
  --schema-file questions.json --evidence-file item-0042.json
```

The evidence is never followed as instructions, so raw email or chat text is
safe to pass. Keep what passed, and turn each into a row with exactly the
table's columns. What failed gets no row: a row in the work table is a unit
of work, and a flag column for the rest would mean changing the measure. Say
in your reply how many you left out and why.

## 4. Import them

```bash
lemma records import conversations rows.jsonl
```

The default `--intent import` keeps table triggers from firing on old rows, so
backfill does not set off drafts or alerts. Check the count with `pod_query`.

## 5. Keep new work coming in

A timed schedule that re-reads the source misses things between runs and
repeats work. Listen for the service's event instead:

```bash
lemma connectors triggers list intercom -q "new conversation"
lemma schedules create --name new-conversations --agent POD_DEFAULT \
  --webhook-source composio --connector intercom \
  --connector-trigger <the trigger id from the list> \
  --filter "A customer asking us for something, not spam or our own reply." \
  --instruction "Add this to conversations, one row, the way the setup did."
```

`--filter` decides whether the run happens at all, so routine noise costs
nothing. When the service has no trigger, a timed schedule that reads only
what is new since the last row is the fallback; say so.

## 6. Count it

A template's measure already counts its tables, this week, only what is due:
keep it, and check that it reads now. The number is read again through the
week and kept as that week's value, so a rolling window ("the last 7 days")
makes every week overlap the one before; never swap one in. If it gets
something wrong, change it and say what and why in your reply. Otherwise,
write one with `set_what_you_own`:

- one read-only query, one row, one column;
- only over tables with `enable_rls: false`, because the number is the same
  for everyone and it may only count rows you can read in full;
- a bar every week counts this week: `WHERE opened_at >= date_trunc('week', now())`;
- count only what is due: an item whose deadline has not passed yet is
  neither a hit nor a miss, so leave it out until it is one or the other;
- with `unit` "%" it answers 0 to 100, as the target is written;
- a week with nothing due answers null, never a number made up for it
  (`coalesce(…, 0)` says everyone was missed, `case … then 100` says nobody
  was);
- keep the person's sentence; a finish line also needs `due`.

The tool reads the measure once and returns `value`. A null or an error means
the query is wrong; fix it now, not later.

## 7. Show it

Always show the weekly number for the weeks you imported, as a widget over
the work table, not only a sentence:

```html
<p id="f" style="font:500 16px/1.4 var(--lemma-widget-font,system-ui);margin:0 0 12px">…</p>
<div id="v"></div>
<script>
lemma.query("select date_trunc('week', opened_at)::date as week, 1.0 * count(*) filter (where answered_in_time) / nullif(count(*) filter (where answered_at is not null or opened_at < now() - interval '1 day'), 0) as share from conversations group by 1 order by 1").then(function (rows) {
  document.getElementById("f").textContent = "…the last four weeks against the target…";
  lemma.line("v", rows, { x: "week", y: "share", format: "pct" });
}, function (e) { document.getElementById("f").textContent = e.message; });
</script>
```

Give it a `fallback` line with the numbers.

## 8. Fill in the plan

Edit `/plan.md` with `pod_edit_file`. Keep its four sections. Each line is one
thing that is true or done, and ends with who said it and when:
`- Refunds over $300 go to Priya — Priya, Nov 7`. Sign with the person's
first name, or the Lemma's name for a line you decided; never an email
address or a role. Write only lines about
things that exist here or that you just set up. Ask who holds what if you do
not know, with choices drawn from `list_pod_members`.

## Finish

Reply in a few lines, with the weekly widget from step 7 displayed in the
same turn: what now feeds it, how many weeks came in, the number against the
target, and the one thing that needs the person, if any. Do not list every
command you ran.
