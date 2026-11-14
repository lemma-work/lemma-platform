---
name: lemma-setup
description: "Set a Lemma up on its work: find where the work happens, connect it, bring in the last few weeks, keep new work coming in, and keep how the work is done in its shared memory. Use in a Lemma's setup chat (\"Set yourself up\"), when someone asks a Lemma to take on work that happens somewhere else, or when its work table is empty because nothing feeds it. Do not use to design a new pod's resources from scratch (lemma-builder) or for day-to-day work in a Lemma that is already set up (lemma-user)."
---

# Set up a Lemma

A Lemma is set up when three things are true:

1. Its work arrives in its work table on its own, one row per unit of work.
2. The last few weeks of that work are already there, so it is useful on the
   first day.
3. Its shared memory says how the work is done, who holds what, and what
   never happens.

Every question you ask during setup should change one of those three, or the
people, connections, skills or memory behind them. An answer that changes
nothing was a wasted question. What you find while reading the work (who is
waiting, what is late) is work to do after setup, not part of it.

**The work table** holds one row per unit of work: `conversations` for
Support, `leads` for Follow-ups, `pull_requests` for Reviews. The examples
below say `<work-table>`; use the real one.

## 0. Look before you ask

Read what is already here, in one turn:

- the runtime context: what it is for;
- `pod_tables`, and the pod-shared memory index already in your brief;
- `list_skills`: a template brings a skill that says how its work is kept, so
  load it;
- `lemma connectors pod-list` and `lemma schedules list`.

A Lemma started from a template already has its work table and timed
schedules, and a `set-up` skill written for that work: follow it, and
come back here only for what it leaves out. It does not have a source:
nothing fills that table yet.

A Lemma started without a template has none of it. Make the work table now,
one row per unit of work, with the columns the work needs (when the item came
in, when it was done or answered, who holds it), a unique `link` for the
item's id at its source, and shared so everyone in the Lemma sees the same
rows:

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
the way in instead: a form people fill in (`lemma-form`), or a memory line
that says the Lemma adds a row whenever someone tells it about one. Then go
to step 6.

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
row: a row in the work table is a unit of work, not a flag on something that
was not one. Say in your reply how many you left out
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

A row has to change when its item is answered or finished, not only appear,
or the table says work is waiting that is done. Listen for that too:
one schedule per trigger (a new conversation, a reply to one), each told to
update the row when its `link` is already there. When the service has no
trigger for one of them, a timed schedule that reads only what is new or done
since its last run is the fallback; say so, and how far behind the table can
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

## 6. Keep what you were taught

Write it to the pod-shared memory, as your memory rules say: a topic file in
`/memory` with a pointer in `/memory/AGENTS.md`, reading the file first when a
pointer already covers it (a template starts one, `how-we-work.md`). Keep how
the work is done, who holds what, and what never happens: one line each, like
`- Refunds over $300 go to Priya`. Write only lines about things that exist
here or that you just set up. Ask who holds what if you do not know, with
choices drawn from `list_pod_members`. A procedure with steps is a skill, not
a memory line.

## Finish

Reply in a few lines: what now feeds it, how many weeks came in, what is
waiting now, and the one thing that needs the person, if any. Do not list
every command you ran.
