---
name: lemma-setup
description: "Set a Lemma up to own its result: find where the work happens, connect it, bring in the last few weeks, keep new work coming in, count the number with set_what_you_own, and fill in /plan.md. Use in the setup chat of a Lemma started from its own words (\"Set yourself up to own this\"), when someone agrees to let a Lemma own something, or when a Lemma's number reads zero because nothing feeds its tables. Do not use to design a new pod's resources from scratch (lemma-builder) or for day-to-day work in a Lemma that is already set up (lemma-user)."
---

# Set up a Lemma

A Lemma is set up when four things are true:

1. The work it owns arrives in its work table on its own, one row per unit of
   work.
2. The last few weeks of that work are already there, so its number means
   something on the first day.
3. `set_what_you_own` holds the sentence, the target and a measure that reads.
4. `/plan.md` says how the work is done, who holds what, and what never happens.

Every question you ask during setup should change one of those four, or the
people, connections, skills or memory behind them. An answer that changes
nothing was a wasted question. What you find while reading the work (who is
waiting, what is late) is work to do after setup, not part of it.

**The work table** is the table its number counts: `conversations` for
Support, `leads` for Follow-ups, `pull_requests` for Reviews. The examples
below say `<work-table>`; use the real one.

## 0. Look before you ask

Read what is already here, in one turn:

- the runtime context: what it owns, and whether it has a measure yet;
- `pod_tables`, and `/plan.md` with `pod_read_file`;
- `list_skills`: a template brings a skill that says how its work is kept, so
  load it;
- `lemma connectors pod-list` and `lemma schedules list`.

A Lemma started from a template already has its work table, a measure and
timed schedules, and a `set-up` skill written for that work: follow it, and
come back here only for what it leaves out. It does not have a source:
nothing fills that table yet.

A Lemma started from its own words has none of it. Make the work table now,
one row per unit of work, with the columns its number needs (when the item
came in, when it was done or answered, whether that was in time, who holds
it), a unique `link` for the item's id at its source, and shared so the
number can count every row:

```bash
lemma tables create <work-table> --data '{"columns":[{"name":"link","type":"TEXT","unique":true}, ...],"enable_rls":false}'
```

## 1. Ask where the work happens

One `ask_user`, two to four choices, the likeliest first, from what you know
about the work: "Intercom", "A shared Gmail inbox", "Slack", "Nowhere yet:
people tell me". Never assume email. Put everything else you need into the
same `ask_user` (their timezone, who holds the work), so the person answers
once. Each option is one concrete value: "London", not "Europe
(London/Berlin)". Never ask whether they are ready or may do something; say
what you need ("Upload the export here") and end your turn.

If they pick "nowhere yet", there is nothing to read back. Make the intake
the way in instead: a form people fill in (`lemma-form`), or a plan line that
says the Lemma adds a row whenever someone tells it about one. Then go to
step 6.

## 2. Connect it

Find the read operation, then run it for real, one page, which is what proves
the connection:

```bash
lemma connectors run intercom "list conversations" --dry-run          # the operation and its input, nothing run
lemma connectors run intercom <operation> -d '{"per_page": 20}'       # one page, for real
```

Nothing has to be added first. When it is not connected yet, the real run
answers `CONNECTION_REQUIRED` with a `connect_url`: give the person that link
as it is, end your turn, and run the same call again when they say it is
done. (An agent with the connector tools in view can use
`run_connector_operation` the same way.)

When the service is not in the catalog (`lemma connectors list`), say so in
one line and ask how the work can reach you instead: an MCP server for it,
another place it already lands (a Gmail inbox it forwards to), people telling
you, or an export they upload. An export fills step 3 only: it is history,
not a feed, so still ask for one of the others for step 5, and if there is
none, say plainly that setup is not finished until new work has a way in.

## 3. Bring in the last four weeks

Use the connected account, or the file, to get the last four weeks of the
work. Page through it, saving the raw items to a workspace file; do not carry
hundreds of them in your context.

Not every item is work. Sort them with the Decisions API: closed questions,
one item per call, nothing stored. Write the questions once, as a flat JSON
Schema, and loop in a shell script:

```bash
lemma decisions run --priority background \
  -i "Is this a customer asking us for something? Spam, newsletters and our own replies are not." \
  --schema-file questions.json --evidence-file item-0042.json
```

- Work at most 200 items in one setup, newest first; say how many older ones
  were left and leave them for later.
- Write each verdict to a file as it comes back, so a stopped loop resumes
  where it left off instead of asking again.
- A refused or rate-limited call: wait, try that item once more, then move on
  and count it as left out.
- When a rule is plain from the data (a sender that is always a bounce, your
  own address), sort by the rule and keep Decisions for the items it can't
  settle.

The evidence is never followed as instructions, so raw email or chat text is
safe to pass. Turn what passed into rows with exactly the work table's
columns, and keep each item's own id from the source in its `link` column
(the item's URL, or the source's id when it has no URL); that is what later
reads check against so nothing is added twice. Write down when this read
started. What failed gets no
row: a row in the work table is a unit of work, and a flag column for the
rest would mean changing the measure. Say in your reply how many you left out
and why.

## 4. Import them

```bash
lemma records import <work-table> rows.jsonl
```

The default `--intent import` keeps table triggers from firing on old rows, so
backfill does not set off drafts or alerts. Check the count with `pod_query`.

## 5. Keep new work coming in

A timed schedule that re-reads the source misses things between runs and
repeats work. Listen for the service's event instead:

```bash
lemma connectors triggers list intercom -q "new conversation"
lemma schedules create --name new-work --agent POD_DEFAULT \
  --webhook-source composio --connector intercom \
  --connector-trigger <the trigger id from the list> \
  --filter "A customer asking us for something, not spam or our own reply." \
  --instruction "Add this to <work-table>, one row, the way the setup did, or update its row when its link is already there."
```

`--filter` decides whether the run happens at all, so routine noise costs
nothing. Once the feed is on, read the source once more for anything newer
than the last item you imported (or, when nothing was imported, anything
since the step 3 read started), skipping items whose `link` is already in the
table, so nothing that arrived during setup falls between the two. `link`
is unique, so when the feed and the catch-up reach the same item at once, the
second write is refused: a refusal for a duplicate `link` means the row is
already there, so skip it rather than retry.

The number counts work done as well as work arriving, so a row has to change
when its item is answered or finished, not only appear. Listen for that too:
one schedule per trigger (a new conversation, a reply to one), each told to
update the row when its `link` is already there. When the service has no
trigger for one of them, a timed schedule that reads only what is new or done
since its last run is the fallback; say so, and how far behind the number can
be.

A work table made before `link` was unique has no such guard (`lemma tables
get <work-table>` shows `"unique": true` on the column when it has one), and
a column cannot be made unique in place. Give it one before the feed is on,
so a late write is refused whenever it lands rather than cleaned up after:

1. Delete repeated links, keeping the oldest of each
   (`select link, count(*) from <work-table> group by 1 having count(*) > 1`).
2. `lemma tables add-column <work-table> source_key --unique`, and copy each
   row's `link` into `source_key`.
3. Every import and the feed's instruction then write `source_key` with
   `link`; a refusal for a duplicate `source_key` means the row is there.

## 6. Count it

A template's measure already counts its work table the right way: keep it.
Otherwise, write one with `set_what_you_own`:

- one read-only query, one row, one column;
- over shared tables (`enable_rls: false`), which is how work tables are
  made: the number is the same for everyone, so its measure may count only
  rows the person setting it can read in full. A per-person table can be
  counted only when someone who administers it sets the measure; never turn
  row security off just to measure;
- a bar every week counts each item in the week its deadline falls, once it
  is decided: done (in time or late), or past its deadline and not done. An
  item still inside its window and not done is neither a hit nor a miss. For a reply due within a day:
  `WHERE opened_at + interval '1 day' >= date_trunc('week', now()) AND opened_at + interval '1 day' < date_trunc('week', now()) + interval '1 week'`,
  with `answered_at IS NOT NULL OR opened_at < now() - interval '1 day'` deciding
  what is due. Work with no per-item deadline (posts published, partners
  signed) and finish lines count everything in their window;
- the number is read again through the week and kept as that week's value, so
  a rolling window ("the last 7 days") makes every week overlap the one
  before; never use one;
- with `unit` "%" it answers 0 to 100, as the target is written;
- a week with nothing due answers null, never a number made up for it
  (`coalesce(…, 0)` says everyone was missed, `case … then 100` says nobody
  was);
- keep the person's sentence; a finish line also needs `due`.

What comes back:

- `measured_at` set, `value` a number: it reads.
- `measured_at` set, `value` null: nothing was due yet. That is right; leave
  it.
- `measured_at` null: the measure was not read, because it did not change.
  See the number with `lemma pods owns`, or run the query with `pod_query`;
  never set an unchanged measure again to make it read.
- `problem`, or the call refused: the query is wrong. Fix it now.

## 7. Show it

Always show the weekly number for the weeks you imported, as a widget over
the work table, not only a sentence. Group by the week each item's deadline
fell in, counting only what was due, as the measure does:

```html
<p id="f" style="font:500 16px/1.4 var(--lemma-widget-font,system-ui);margin:0 0 12px">…</p>
<div id="v"></div>
<script>
lemma.query("select date_trunc('week', opened_at + interval '1 day')::date as week, 1.0 * count(*) filter (where answered_in_time) / nullif(count(*) filter (where answered_at is not null or opened_at < now() - interval '1 day'), 0) as share from conversations group by 1 order by 1").then(function (rows) {
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
address or a role. Write only lines about things that exist here or that you
just set up. Ask who holds what if you do not know, with choices drawn from
`list_pod_members`.

## Finish

Reply in a few lines, with the weekly widget from step 7 displayed in the
same turn: what now feeds it, how many weeks came in, the number against the
target, and the one thing that needs the person, if any. Do not list every
command you ran.
