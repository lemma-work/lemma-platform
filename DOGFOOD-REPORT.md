# Lemma CLI dogfood report — building a customer-support pod on `asur`

**Date:** 2026-08-04 · **Branch:** `lemma/lemma-cli-grants-consistency-6e0611` (`0c4f71f2`)
**Server:** `asur`, org "anukul@gappy.ai's Personal", user `anukul@gappy.ai`
**Every command was run from the branch worktree** via
`uv run --project .../trusting-engelbart-d21c57/lemma-cli lemma <args>`
(abbreviated as `lemma <args>` below).

---

## Summary

### What I built

| | |
| --- | --- |
| **Pod** | `helpdesk-dogfood` — `019fccc8-23fe-7122-a3c5-2f6f579213d8` |
| **Round-trip target pod** | `helpdesk-dogfood-copy` — `019fccda-e3b0-70ab-818a-e6491799498d` |

Both left in place. Contents of `helpdesk-dogfood`:

- **Tables (3, shared/RLS-off):** `tickets`, `replies` (FK → `tickets.id`), `customers`
- **Files:** `/support-knowledge` with `refund-policy.md`, `sla-matrix.md`, `escalation-playbook.md` (indexed, searchable)
- **Functions (3):** `score_ticket` (severity/priority/category → writes back), `file_ticket` (multi-table write: ticket + customer upsert), `close_ticket` (created live via `functions create -f` with inline grants)
- **Agents (2):** `support-triage` (POD + CONNECTORS, 12 grants, `output_schema`), `policy-lookup` (sub-agent, folder-only grant, `output_schema`)
- **Workflows (2):** `ticket-intake` (FORM → FUNCTION → FUNCTION → AGENT → FORM → DECISION → 2×END, MANUAL), `auto-triage` (DATASTORE_EVENT on `tickets` INSERT)
- **Schedules (2):** `on-new-ticket` (DATASTORE, active, fired for real), `hourly-inbox-sweep` (TIME cron, inactive)
- **App (1):** `support-console`, single-file HTML, status `READY`
- **Grants exercised:** table `read`/`read,write`, folder `/path:read`, `doc:/path:read`, `connector:gmail:use`, `account:<id>:use` (pinned/fixed-account), `function:*:execute`, `agent:*:execute`, `workflow:*:execute`, `app:*:read`
- **Live data:** 3 tickets, 2 replies, 2 customers, 1 completed human-approval workflow run, 2 completed datastore-triggered runs

### What genuinely works well

The core loop is real and it is good. These all worked **first try**, against a live API:

- `lemma <resource> init` scaffolds for every resource type; `lemma pods import` upsert; the deferred-permissions pass (my `/support-knowledge` folder grant resolved even though folders import **last**).
- **Every grant spec in the documented grammar** landed on a live pod in **one** `permissions add` call, including `account:<uuid>:use`, `doc:/path:read`, `workflow:x:execute`, `app:x:read`.
- **Inline grants on `functions create`** land, and work at runtime (item #1 — the headline fix — is solid).
- **DATASTORE trigger → workflow → function** end-to-end, with `start.metadata.record_id` populating correctly, in ~8 s. Schedule telemetry (`last_fire_status: TRIGGERED`, `last_run_id`) is exactly as documented.
- **Sub-agent as a tool**: `support-triage` called `agent_policy-lookup` and got back the child's `output_schema` dict. The whole grant → tool → typed-result chain works.
- **Human-in-the-loop workflow**: FORM with a dynamic `default` bound to the agent's output, `submit-form`, DECISION routing to the correct END.
- **Connector one-call run**: `connectors run gmail GMAIL_FETCH_EMAILS -d '...'` returned real mail in one call.
- **`--var` / `${...}` export scrubbing** of the pinned account id into `pod.json` is exactly the right design.

### Headline problems

1. **`MISSING_WORKLOAD_RESOURCE_GRANT` does not name the resource** — four separate skill files promise it does. It names only a permission id.
2. **A full export → import round trip silently kills the pod's RAG.** `--with-files` re-uploads `.md` documents as `NOT_REQUIRED` / not searchable. The copy pod's `files search` returns "No results"; both its agents depend on that search.
3. **`pods import --dry-run` does not catch two of the three failure classes it exists to catch** — a bad schedule `config` and an unrecognized field both pass dry-run and then abort the real import *mid-write* (no transactions).
4. **The pinned-account variable resolves silently to the source org's account id when `--var` is omitted**, instead of being dropped with a warning as documented.
5. **`connectors operations search "send email"` is broken and `--help` says it works.**
6. **`--output json` is unusable for piping** — warnings and advisories go to stdout and break every JSON parse.
7. **The active server is a global mutable file.** It flipped from `asur` to `lemma-cloud` mid-session and every error I got was `AGENT_NOT_FOUND` / `INSUFFICIENT_PERMISSION` / `POD_NOT_FOUND` — never "you are pointed at a different server". I nearly filed a bogus grants bug.

---

## Verification of this branch's changes

| # | Change | Verdict |
| --- | --- | --- |
| 1 | `functions create -d` with `permissions.grants` | **WORKS** |
| 2 | `permissions add\|remove` live; `replace --from-bundle` | **WORKS** |
| 3 | Grant spec grammar | **WORKS** |
| 4 | Zero-grant advisory on `import --dry-run` and `doctor` | **WORKS** |
| 5 | Unknown field warns ad-hoc / hard-fails a bundle | **PARTIAL** — dry-run misses it |
| 6 | Pinned account → `${var}` on export, sane import | **PARTIAL** — export perfect, import wrong |
| 7 | `connectors run` in one call | **WORKS** (with a bad resolver default) |
| 8 | `operations search "send email"` with no auth-config | **BROKEN** |
| 9 | Full export → import round trip | **PARTIAL** — structure perfect, files/data broken |

### 1. Inline grants on `functions create` — **WORKS**

```
$ lemma functions create -f /tmp/close_ticket.json      # payload has permissions.grants (2 tables)
… Function created (close_ticket)

$ lemma --output json functions permissions get close_ticket
[('datastore_table','tickets',  ['datastore.record.read','datastore.record.write','datastore.table.read']),
 ('datastore_table','replies',  ['datastore.record.read','datastore.record.write','datastore.table.read'])]
```

And they work at runtime — `lemma functions run close_ticket -d '{"ticket_id":"cca7d1f1-…"}'` →
`Status COMPLETED`, and it wrote to **both** granted tables. `functions create --help` is honest about
the mechanism ("the function create endpoint takes no inline permissions … applied right after the create").

### 2. `permissions add` / `remove` / `replace --from-bundle` — **WORKS**

`add` with five specs at once, on a live pod, one call:

```
$ lemma agents permissions add support-triage \
    account:019f893d-8d43-7100-bc72-06d51d36623d:use function:close_ticket:execute \
    workflow:ticket-intake:execute app:support-console:read doc:/support-knowledge/refund-policy.md:read
permissions added to agent support-triage — now:
  …
  connector_account 019f893d-8d43-7100-bc72-06d51d36623d: connector_account.use
  function close_ticket: function.execute
  workflow ticket-intake: workflow.execute
  app support-console: app.read
  document /support-knowledge/refund-policy.md: folder.read
```

`remove` works (verified the doc grant disappeared). `replace --from-bundle` works: I added a stray
`tickets:read` to `policy-lookup`, then `lemma agents permissions replace policy-lookup --from-bundle ./helpdesk-dogfood`
correctly reduced it back to the bundle's single folder grant.

Two nits: (a) `add`/`remove` print a friendly full grant list *with* permission ids, `replace` prints a
folded `Details` panel — inconsistent; (b) `remove` of a grant that doesn't exist
(`lemma agents permissions remove support-triage table:nope:read`) is a **silent no-op**, so a typo looks like success.

### 3. Grant spec grammar — **WORKS**

All nine forms landed: `tickets:read,write`, `/support-knowledge:read`, `doc:/support-knowledge/refund-policy.md:read`,
`connector:gmail:use`, `account:<id>:use`, `function:score_ticket:execute`, `agent:policy-lookup:execute`,
`workflow:ticket-intake:execute`, `app:support-console:read`. Type inference (leading `/` = folder,
bare = table) behaved as documented.

### 4. Zero-grant advisory — **WORKS**

On `import --dry-run` (this is the *only* reason I caught that `functions init` scaffolds an empty grants list):

```
advisory function 'score_ticket' declares an empty grants list, so it will be created with NO access —
it cannot read any table, folder, or connector and will fail with MISSING_WORKLOAD_RESOURCE_GRANT at
runtime. Add permissions.grants, or run `lemma functions grant score_ticket <resource>:<perms>`.
```

On `pods doctor` (probe agent created with `"grants": []`):

```
warn   agent 'zero-probe' holds NO grants — it cannot read any table, folder, or connector.
       Grant it with `lemma agents permissions add zero-probe <resource>:<perms>`.
warn   agent 'zero-probe' has the SUBAGENTS toolset but no agent grants — self-spawn only;
       grant `agent:<other>:execute` to fan out.
```

Both messages are excellent: they name the workload, the consequence, and the exact fix.

### 5. Unknown/typo'd field — **PARTIAL**

Ad-hoc warns, correctly and helpfully:

```
$ lemma agents update policy-lookup -d '{"descriptionn": "typo probe", "toolsetz": ["POD"]}'
warning (agent policy-lookup): ignored unrecognized field(s) descriptionn, toolsetz — the API has no
such field, so they were NOT sent. Run `lemma agent schema` for the required fields and valid enums.
```

Bundle **real** import hard-fails, correctly:

```
$ lemma pods import ./probe/agents/policy-lookup
agent updating policy-lookup
Unrecognized field(s) (agent policy-lookup): toolsetz. The API request has no such field, so they
would be dropped silently. Run `lemma agent schema` for the required fields and valid enums.
```

But `--dry-run` on the *same bundle* passes clean:

```
$ lemma pods import ./probe/agents/policy-lookup --dry-run
  agents     updated   policy-lookup
OK
```

That is the failure mode dry-run exists to prevent. See Finding **F3**.

### 6. Pinned connector account → export/import — **PARTIAL**

**Export: perfect.** `pod.json` after `lemma pods export ./export --force`:

```json
"support_triage_account": {
  "connector": "gmail", "connector_kind": "composio",
  "default": "019f893d-8d43-7100-bc72-06d51d36623d",
  "description": "Connector account for agents 'support-triage'",
  "source_value": "019f893d-8d43-7100-bc72-06d51d36623d", "type": "account"
}
```

and the grant became `"resource_name": "${support_triage_account}"` with `connector_id`/`connector_kind`
carried alongside. Exactly right.

**Import without `--var`: wrong.** Docs (authorization-model §4b, cli-and-bundles "Limits And Gotchas")
say the grant is *dropped with a warning* and the workload falls back to user-resolved mode. Actual:

```
$ lemma pods import ./export/helpdesk-dogfood --pod 019fccda-…   # no --var
agent replacing permissions (12 grant(s): … connector_account:019f893d-8d43-7100-bc72-06d51d36623d, …)
```

The variable silently resolved to its `default`, i.e. the **source org's** account id, and the grant
landed verbatim in the target pod. Verified:

```
$ lemma --output json agents permissions get support-triage --pod 019fccda-…
connector_account | 019f893d-8d43-7100-bc72-06d51d36623d
```

Runtime behaviour of the pinned account itself is fine — `lemma agents run support-triage "fetch the 2
most recent messages…"` went through the Gmail connector and returned real mail.

### 7. `connectors run` in one call — **WORKS**, but the resolver picks a dangerous default

```
$ lemma connectors run gmail "list recent emails" --dry-run
Resolved operation: 'list recent emails' -> GMAIL_ADD_LABEL_TO_EMAIL
  (also matched: GMAIL_BATCH_DELETE_MESSAGES, GMAIL_FETCH_EMAILS, …)
```

This is the **verbatim example from both `lemma-user/SKILL.md` and `cli-and-bundles.md`**, and it
resolves a read intent to a *mutating* operation. See Finding **F4**. Naming the op explicitly works
perfectly and returned real mail in one call:

```
$ lemma --output json connectors run gmail GMAIL_FETCH_EMAILS -d '{"max_results":3,"include_payload":false}'
Ran gmail / GMAIL_FETCH_EMAILS   → 3 messages with messageId/subject/sender/messageTimestamp
```

**Call count:** 2 (schema + execute), or 1 when the op id is known — versus the old
overview → search → get → execute = 4. Real improvement.

### 8. `operations search "send email"` with no auth-config — **BROKEN**

```
$ lemma connectors operations search "send email" --limit 5
Invalid value for AUTH_CONFIG: Several connectors are installed — name one (or pass its connector id):
telegram (…), outlook (…), slack (…), whatsapp (…), canva (…), resend (…), teams (…), gmail (…),
github (…), google_docs (…)
```

And `--help` for that exact command says:

> ``lemma connectors operations search "send email"`` **works**: a lone positional that doesn't name
> an installed connector is treated as the query, not as the auth config.

It does not. The fallback only fires when exactly **one** connector is installed
(`--auth-config … Auto-discovered when only one is installed`). Any real org has more. See **F5**.

### 9. Full export → import round trip — **PARTIAL**

Structure round-trips beautifully: 3 tables, 3 functions, 2 agents, 2 workflows, 2 schedules, 1 app,
1 folder, **all 12 grants on `support-triage` and both function grant sets** re-applied in the new pod.
The app came back as `apps/support-console/dist.zip` (not `html.html` as documented) and still
re-imported to `READY`.

Two real defects, both silent — see **F1** (files come back unindexed) and **F8** (`--with-data` is a
no-op on existing tables).

---

## Findings

### F1 · BLOCKER · Round-tripped files are stored but never indexed, so the copied pod's RAG is dead

```
$ lemma pods export ./export2 --with-files --with-data --force      # files: 3
$ lemma pods import ./export2/helpdesk-dogfood --pod 019fccda-… --with-files --with-data --var …
  files      uploaded-file   support-knowledge/refund-policy.md    (×3)

$ lemma files stat /support-knowledge/refund-policy.md --pod 019fccc8-…   # SOURCE
│ Status COMPLETED          │ Search Enabled yes
$ lemma files stat /support-knowledge/refund-policy.md --pod 019fccda-…   # COPY
│ Status NOT_REQUIRED       │ Search Enabled no

$ lemma files search "duplicate charge refund" --scope /support-knowledge --pod 019fccda-…
No results.
```

The identical `.md` bytes uploaded with `lemma files upload` index to `COMPLETED`/searchable. Through
export→import they land as `NOT_REQUIRED`. Both agents in the copy pod are built around searching
`/support-knowledge`; they now silently answer from nothing. `pods doctor helpdesk-dogfood-copy` still
says `ok no errors`. And the `lemma-user` troubleshooting text actively misleads here — it says
`NOT_REQUIRED` "means it isn't an indexed document (CSV/JSON/XLSX/images/email…)", which a `.md` file
plainly is not.

**Fix:** import's file upload should use the same indexing path/default as `files upload` (i.e. index
unless `--no-search`). Add a `doctor` check for granted folders whose documents are all unindexed.

### F2 · BLOCKER · `MISSING_WORKLOAD_RESOURCE_GRANT` does not name the resource

Removed `customers` from `file_ticket` in the copy pod, then ran it:

```
$ lemma functions run file_ticket -d '{"subject":"grant probe","customer_email":"probe@example.com","body":"probe"}' --pod 019fccda-…
│ Status FAILED
│ Error LemmaPermissionError: [403] MISSING_WORKLOAD_RESOURCE_GRANT: Missing permission
│ datastore.table.read (request_id=e0e6266e-…)
```

It names the *permission id*, not the *resource*. `file_ticket` touches two tables; nothing in the
error says which one. Four skill files promise otherwise:

- `authorization-model.md` §4: "The message names the resource."
- `functions.md`: "names the resource it tried to reach — add exactly that grant and retry."
- `agents.md`: "fails with `MISSING_WORKLOAD_RESOURCE_GRANT` naming the resource."
- `lemma-user/SKILL.md`: "names a missing **workload grant**".

This is the single most-cited error in the docs and the recovery instructions depend on information the
error does not carry.

**Fix:** include `resource_type` + `resource_name` in the message, e.g.
`Missing permission datastore.table.read on datastore_table 'customers' — grant it with
lemma functions permissions add file_ticket customers:read`.

### F3 · MAJOR · `pods import --dry-run` passes bundles that the real import rejects mid-write

Two independent cases, both found by accident:

**(a) Schedule config.** `lemma schedules init` scaffolds this comment verbatim:

```jsonc
"config": { "cron": "0 9 * * *" },// TIME: cron; DATASTORE: {"datastore":"<table>","operations":["INSERT"]}
```

`schedules-and-triggers.md` says the key is `table_name`. I followed the scaffold. `--dry-run` printed
a clean 12-row plan and `OK`. The real import then created 11 resources and died on the 12th:

```
$ lemma pods import .
… table created customers … agent created support-triage … workflow created ticket-intake …
schedule creating on-new-ticket
[422] VALIDATION_ERROR: Request validation failed (request_id=0ecc5707-…)
  - table_name: Field required
```

**(b) Unrecognized field** — see verification item #5 above: dry-run `OK`, real import aborts.

Because there are no transactions ("Import fails fast on the first error and leaves prior resources
applied"), dry-run is the *only* safety net, and it doesn't cover the two most likely authoring
mistakes. The 422 also doesn't name the bundle file or the offending key (`datastore`); you have to
infer the resource from the preceding progress line.

**Fix:** validate `schedule.config` per `schedule_type` and run the unknown-field check during
`--dry-run`; prefix bundle validation errors with the source path.

### F4 · MAJOR · The documented `connectors run` example resolves a read intent to a write operation

```
$ lemma connectors run gmail "list recent emails" --dry-run
Resolved operation: 'list recent emails' -> GMAIL_ADD_LABEL_TO_EMAIL
```

`GMAIL_FETCH_EMAILS` is in the "also matched" list, ranked third. This exact string is the example in
`lemma-user/SKILL.md` and `cli-and-bundles.md`. `--dry-run` saved me; a caller who omitted it and
supplied a plausible `-d` would have mutated labels. The skill explicitly reassures that omitting
`--data` is safe ("prints the input schema instead of failing") — that only holds because
`message_id` happens to be required on the op it wrongly picked.

**Fix:** bias the resolver toward read/list verbs for read-shaped queries; and refuse to auto-resolve
natural language to a *mutating* operation without confirmation (or at minimum print a
"this is a write operation" banner).

### F5 · MAJOR · `connectors operations search "<query>"` is broken, and `--help` asserts it works

Evidence in verification item #8. The help text is a load-bearing lie: an agent reading `--help`
will write the one-positional form, get a hard error, and have to discover the two-positional form.

**Fix:** implement the documented fallback (a lone positional that matches no installed connector id
should search across **all** installed connectors), or correct the help text to say it only applies
with a single install.

### F6 · MAJOR · `--output json` is polluted by warnings on stdout

```
$ lemma --output json agents update policy-lookup -d '{"bogusfield":1}' 2>/dev/null | python3 -c "import json,sys; json.load(sys.stdin)"
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

The warning line is on **stdout**, ahead of the JSON. Same class of failure hit
`lemma --output json pods create …` (`JSONDecodeError: Extra data: line 1 column 7`). Both skills say
to use `--output json` "only to pipe/save" — but you cannot pipe it safely, because any advisory
(zero-grant, connector-grant, unknown-field) lands in the same stream. Every `pods import`,
`functions create`, `agents create` is affected.

Separately, the JSON envelope is inconsistent: `pods list` returns a bare array, `conversations list`
returns `{"items": [...]}`.

**Fix:** route all warnings/advisories/progress to stderr; give every list command the same envelope.

### F7 · MAJOR · Active server is global, mutable, and its failure mode is unrecognisable

Mid-session, `~/.lemma/config.json`'s `active_server` changed from `asur` to `lemma-cloud`. My next
four commands returned:

```
$ lemma agents run policy-lookup "…"      → [404] AGENT_NOT_FOUND: policy-lookup
$ lemma agents list                        → [403] INSUFFICIENT_PERMISSION: Missing permission agent.read
$ lemma pods describe                      → [403] INSUFFICIENT_PERMISSION: Missing permission pod.read
$ lemma pods members                       → [404] POD_NOT_FOUND: Pod not found
$ lemma query run "select count(*) from tickets"  → [404] DATASTORE_TABLE_NOT_FOUND: Table 'tickets' not found
```

Not one of them mentions the server. `INSUFFICIENT_PERMISSION: Missing permission agent.read` in
particular sent me looking for a grants bug in the feature under test. I only found it because
`lemma auth status` printed a **different user id** than it had 20 minutes earlier. The fix is
`LEMMA_SERVER=asur` (or `--server`), which is documented — but nothing in the failure points there.

**Fix:** when `LEMMA_POD_ID` (or a stored pod default) is set and the pod 404s on the active server,
say so: `pod 019fccc8-… not found on server 'lemma-cloud' (active). It exists on 'asur' — pass
--server asur.` At minimum, print the resolved server on any 403/404.

### F8 · MAJOR · `import --with-data` silently does nothing on existing tables

The second round-trip import ran with `--with-data` and three populated `data.csv` files. Output showed
`tables updated` for all three and **no** data lines; `select count(*) from tickets` in the copy → `0`.
`--help` says "(new tables only)" so it is technically documented, but nothing is printed at run time,
and the natural sequence (import structure first, then re-import with data) can therefore **never**
seed rows. There is no `--force-data` / `records import` hint.

**Fix:** emit `skipped data seed for 'tickets' (table already exists; --with-data seeds new tables only)`
and point at `lemma records import tickets ./data.csv`.

### F9 · MAJOR · The skills say file bytes and rows can't bundle; the CLI has flags for both

`lemma pods export --help` / `import --help` document `--with-files`, `--with-data`, `--data-table`.
Meanwhile:

- `cli-and-bundles.md`: "**File contents never travel in bundles** — only folder metadata."
- `lemma-builder/SKILL.md`: "File contents and connectors … are not part of import/export", and
  "(Records and file contents don't round-trip through import — the seed script is how they land)".

So the skill instructs builders to hand-write `seed/seed.sh` for a capability the CLI already ships.
(That said — given **F1** and **F8**, the skill's pessimism is currently *accidentally correct*.)

**Fix:** after fixing F1/F8, rewrite that section of both skill files.

### F10 · MAJOR · A newly created pod is not active, and `pods select` doesn't survive the shell

```
$ lemma pods create helpdesk-dogfood --description "…"      → created
$ lemma pods list                                            → active is still 'cadence'
$ lemma pods select helpdesk-dogfood
pod helpdesk-dogfood — active for this shell only
apply to your shell: eval "$(lemma pods select helpdesk-dogfood -x)"
```

For an agent (every bash call is a fresh shell) `pods select` is a no-op, so **every subsequent command
must carry `--pod` or an exported `LEMMA_POD_ID`**. Nothing in either skill mentions this; `pods select`
isn't even in the `cli-and-bundles.md` cheatsheet. The dangerous case: an agent that runs
`pods create` then `tables create` writes into whatever pod was previously active — someone else's.

**Fix:** have `pods create` print the exact next step (`export LEMMA_POD_ID=…` / `--pod …`), or offer
`--select`/`--write-env` to drop a `.lemma.<server>.env`. Document `pods select` and its shell scoping
in both skills.

### F11 · MAJOR · Sub-agent child conversations are not observable

`support-triage` called `agent_policy-lookup` (confirmed in the parent's message stream). But:

```
$ lemma --output json conversations list --agent policy-lookup   → n: 1   (my own direct run)
$ lemma --output json conversations list                          → n: 2, both parent_id: None
```

`agents.md` says the tool "spawns a real, persisted **child conversation** (linked via
`parent_id`/`parent_run_id`)". Nothing shows up. The only way to see what the sub-agent was asked and
what it answered is to dump the parent conversation's `tool_result` JSON. For a pod whose whole point
is auditable delegation, that's a hole.

**Fix:** persist and list child conversations with `parent_id` set; add `conversations list --parent <id>`.

### F12 · MAJOR · `agents run`/`chat` dump raw chain-of-thought and tool-call JSON with no result-only mode

Every agent invocation prints the full internal stream — reasoning paragraphs plus literal
`{"tool_name":"pod_write_record","args":{…}}` blobs — and the actual answer is the last
`{"tool_name":"final_result","args":{"output":{…}}}` line. A single `agents run` produced ~4 KB of
transcript for a 3-sentence answer. `lemma agents run --help` offers only `--pod`, `--wait/--no-wait`,
`--conversation`, `--title`. There is no `--quiet` / `--result-only` / `--output json`-shaped result.

For an agent operator this is both a token cost and a parsing problem: to get the structured output you
must regex the transcript or fetch `conversations messages --output json` afterwards.

**Fix:** default to rendering the final `output` (pretty for `output_schema` agents), with `--verbose`
for the stream; support `--output json` returning `{status, output, conversation_id}`.

### F13 · MINOR · `permissions get` — the documented verification command — hides the grants

```
$ lemma agents permissions get support-triage
│ Grants 8 items — first: resource_name=policy-lookup, resource_type=agent
… some fields were folded; pass --full for complete output

$ lemma agents permissions get support-triage --full
│ Grants 8 items
│   - resource_name=policy-lookup, resource_type=agent      … (8 lines, still no permission_ids)
```

`authorization-model.md` §9 calls this "one command, not a guess". By default it shows one grant; with
`--full` it shows names but never `permission_ids`, so you cannot tell read from read-write without
`--output json`. Meanwhile `permissions add`/`remove` print the complete list *with* permission ids.

**Fix:** make `permissions get` print what `permissions add` prints.

### F14 · MINOR · `pods doctor` emits an unconditional, unverified warning for every folder grant

```
$ lemma pods doctor
warn   agent 'support-triage' grants folder '/support-knowledge' — verify it exists / will be created.
warn   agent 'policy-lookup'  grants folder '/support-knowledge' — verify it exists / will be created.
ok no errors (4 warning(s)).
```

The folder existed — the same import had just created it, and `lemma files tree /` lists it. Doctor is
documented to "re-check all of these **against the live pod**"; for folders it clearly doesn't. Two of
my four doctor warnings were noise, which is how a linter teaches you to stop reading it.

**Fix:** actually stat the folder; only warn when it's missing.

### F15 · MINOR · `functions|agents init` scaffold an empty `permissions.grants`, guaranteeing the advisory

`lemma functions init score_ticket` writes `"permissions": { "grants": [ /* commented example */ ] }` —
an explicitly **empty** list, which per the documented semantics is a *deliberate* "revoke everything"
signal, not "unset". So every scaffolded workload starts in the state the advisory warns about. The
advisory then fires on the very first `--dry-run` of a brand-new pod.

**Fix:** scaffold with the `permissions` key **commented out** (absent = leave alone), or seed it with
the starter table grant the way `lemma pod init`'s `hello` agent already does.

### F16 · MINOR · `lemma workflows runs waiting` shows nothing for unassigned forms

My `ticket-intake` run was parked on FORM `approve` (confirmed in `runs get`), yet:

```
$ lemma workflows runs waiting
Nothing is waiting on you.
```

The form has no assignee, and per `workflows.md` "With no assignee, any pod member with execute access
can submit" — so it's submittable by me but invisible in my queue. `lemma-user`'s troubleshooting sends
you to `runs waiting` when a workflow is stuck; you find nothing and conclude the run is fine.

**Fix:** include unassigned form waits the caller is entitled to submit (flagged "unassigned"), or make
`runs waiting` say "0 assigned to you; N unassigned waits in this pod".

### F17 · MINOR · The `pod_write_record` agent tool accepts an empty payload and costs 3 turns

From the `support-triage` transcript, three consecutive identical calls:

```
assistant tool_args  {'data': {}, 'action': 'create', 'table_name': 'replies'}
tool      tool_result {'error': '`data` must be a non-empty object of column->value for action=create,
                       e.g. {"title": "..."}. The payload was empty, so nothing was written.',
                       'success': False}
```

The error text is good; the schema is not — `data` is `anyOf [object(properties:{}), string, null]`,
which reads to a model as optional. Three wasted round-trips on a write the agent already knew how to
compose (the 4th call was fully populated on the first attempt).

**Fix:** mark `data` required with `minProperties: 1`, drop the `null` branch, and put a concrete
example in the parameter description.

### F18 · MINOR · Bundle-scaffold text contradicts the docs in three places

From `lemma <resource> init` output, all in this branch:

1. `schedules init` → `// DATASTORE: {"datastore":"<table>","operations":["INSERT"]}` — wrong key,
   causes F3(a).
2. `agents init` → `instruction.md` template says *"name the tables you read/write and the `/pod`
   folders that hold knowledge"* — every skill file states emphatically there is **no `/pod` prefix**.
   A builder who copies the scaffold writes an instruction that sends the agent to non-existent paths.
3. `schedules init` → `// "agent_name": "some-workflow",` — copy-paste, should be `some-agent`.

Also `agents init` lists a `VIEW_IMAGE` toolset that `agents.md`'s toolset table does not contain, and
`pod init` writes `"format_version": 3` while `cli-and-bundles.md` documents `format_version: 1`.

### F19 · POLISH · `pods doctor` is the only command that rejects `--pod`

```
$ lemma pods doctor --pod 019fccda-…
No such option: --pod          # works as: lemma pods doctor helpdesk-dogfood-copy
```

Both skills present `--pod <id-or-slug>` as a universal flag.

### F20 · POLISH · `lemma config show` doesn't show what the skill says it shows

`cli-and-bundles.md`: "`lemma config show` reports the resolved server + applied files." Actual output
is a list of configured server names plus `active server: asur` — no applied-files line, no org, no pod,
no base URL. Given F7, this is the command you'd reach for to debug context, and it under-delivers.

### F21 · POLISH · `schedules list` never shows a workflow target

The list has an `Agent Id` column but no workflow column, so `on-new-ticket` (a workflow schedule)
shows a blank target. `schedules get` has `Workflow Name` — the list should too.

---

## Step-count table

| Outcome | Calls I needed | Should be | Why |
| --- | --- | --- | --- |
| Read 3 recent emails | **2** (`run --dry-run` for schema, `run` to execute) | 1–2 | Working as intended; old path was 4 |
| Create a pod and safely work in it | **3** + `--pod`/env on every later call | 1 | `create` doesn't select; `select` doesn't persist (F10) |
| Verify a workload's grants incl. read-vs-write | **3** (`get` → `get --full` → `--output json`) | 1 | Folded output, no permission ids (F13) |
| Import a bundle containing a DATASTORE schedule | **3** (dry-run OK → import fails at 12/12 → fix → import) | 1–2 | Dry-run gap + wrong scaffold comment (F3, F18) |
| Find out which tables a failing function needed | **3** (run → `permissions get --output json` → read code) | 1 | Error omits the resource (F2) |
| Search operations by intent, no connector named | **2** (fails → retry with connector) | 1 | F5 |
| See what an agent actually did | **2** + local JSON parsing | 1 | No result-only/structured mode (F12) |
| Discover a sub-agent's transcript | **not possible** via `conversations` | 1 | F11 |
| Diagnose "AGENT_NOT_FOUND" (wrong server) | **6** (5 failing commands + `auth status`) | 1 | Errors never mention the server (F7) |
| Confirm a round-tripped pod is actually functional | **4** (`stat` source, `stat` copy, `search` copy, `query`) | 1 (`doctor`) | Doctor doesn't check indexing or seeded rows (F1, F8, F14) |

---

## Skill accuracy

### `lemma-builder/SKILL.md`

- ✅ Build order, the three "rules that bite everyone", and the scaffold-first workflow all held up.
- ❌ "File contents and connectors … are not part of import/export" — contradicted by
  `export/import --with-files`, `--with-data`, `--data-table` (**F9**).
- ❌ `lemma agents grant …` and the resource list are right, but **`lemma pods select` is missing
  entirely**, and nothing warns that a fresh pod isn't active (**F10**).
- ⚠️ "Import and `lemma pods doctor` both flag a workload that ends up with no grants at all" — true,
  and it's the most useful advisory in the product.

### `lemma-builder/references/authorization-model.md`

- ✅ §4b grant grammar table: every row verified working on a live pod.
- ✅ §6/§7 function-as-tool and agent-as-tool: verified, including the child running under its own
  grants and returning its `output_schema` dict.
- ❌ §4 "The message names the resource" for `MISSING_WORKLOAD_RESOURCE_GRANT` — **false** (**F2**).
- ❌ §4b "Unsupplied, the grant is **dropped with a warning**" for a pinned account — **false**; it
  resolves to the exported `default` (**verification #6**).
- ❌ §9 "Verify after import — this is one command": `permissions get` takes three (**F13**).

### `lemma-builder/references/cli-and-bundles.md`

- ✅ Bundle layout, JSONC, `$file`/`$json_file`, folder==name, deferred permissions pass, import order.
- ❌ "File contents never travel in bundles" (**F9**).
- ❌ "`lemma config show` reports the resolved server + applied files" (**F20**).
- ❌ `pod.json` documented as `format_version: 1`; scaffold and export both emit `3`.
- ❌ HTML apps documented to round-trip as `apps/<name>/html.html`; export emits `dist.zip`.
- ⚠️ "Unrecognized fields fail the import" — true for the real import, **not** for `--dry-run` (**F3**).
- ⚠️ Cheatsheet omits `pods select`, `--with-files`, `--with-data`, `--data-table`, `--as-template`
  details, and `--set-pod-meta`.

### `lemma-builder/references/schedules-and-triggers.md`

- ✅ DATASTORE config shape (`table_name` + `operations`) is **correct** — it's the `schedules init`
  scaffold that's wrong (**F18**).
- ✅ `start.metadata.record_id` guidance is correct and the footgun warning is well-placed; my
  workflow read it and worked first try.
- ✅ Telemetry (`last_fired_at`, `last_run_id`, `last_fire_status`) exists exactly as described.

### `lemma-builder/references/workflows.md`

- ✅ Node configs, JMESPath conditions with backticks, DECISION default-edge ordering, dynamic
  `default: {"type":"expression", …, "optional":true}` in a FORM schema — all worked first try.
- ⚠️ Doesn't warn that an **unassigned** FORM never appears in `runs waiting` (**F16**), which is the
  default shape of the approval-gate pattern it recommends.

### `lemma-builder/references/agents.md` / `functions.md`

- ✅ Code header contract, `Pod.from_env()`, response shapes (`.to_dict()["items"]`), grant tables —
  all correct; both my functions ran first try with no source-reading.
- ❌ "spawns a real, persisted child conversation (linked via `parent_id`/`parent_run_id`)" — the child
  is not listed anywhere (**F11**).
- ❌ `MISSING_WORKLOAD_RESOURCE_GRANT` "naming the resource" (**F2**).
- ⚠️ Toolset table omits `VIEW_IMAGE`, which `agents init` advertises.

### `lemma-user/SKILL.md`

- ✅ Files section (search → cat → `--pages`/`--lines` → children) is accurate and pleasant.
- ✅ `query run` with a JOIN across two shared tables worked first try.
- ✅ RLS/delegated-identity explanation matched observed behaviour.
- ❌ `lemma connectors run gmail "list recent emails" --dry-run` resolves to
  `GMAIL_ADD_LABEL_TO_EMAIL` (**F4**).
- ❌ `lemma connectors operations search gmail "send email"` is fine, but the "just a query" form the
  help promises is broken (**F5**).
- ❌ `NOT_REQUIRED` troubleshooting text mis-diagnoses **F1** (`.md` files *are* indexable).
- ⚠️ "Use `--output json` only to pipe/save" — piping is unreliable (**F6**).
- ⚠️ `runs waiting` described as "your approval queue" without the unassigned caveat (**F16**).

### Did I have to read repo source to get unstuck?

**No.** Everything was recoverable from the skills, `--help`, and error text. Two things came *only*
from `--help` and would have been invisible to an agent that trusted the skills: `pods select`
(**F10**) and `--with-files`/`--with-data` (**F9**). One thing came only from a *behavioural* diff
(`lemma auth status` showing a different user id) — the server switch (**F7**).

---

## What I could not test, and why

- **Sending email.** Hard constraint. I resolved `GMAIL_SEND_EMAIL` and its full input schema via
  `connectors run gmail GMAIL_SEND_EMAIL --dry-run` (`recipient_email`/`to`, `subject`, `body`,
  `is_html`, `cc`, `bcc`, `attachment`, `from_email`, `extra_recipients`) and stopped there. The
  agent's send-path boundary ("never send; write a draft to `replies`") is therefore **unverified at
  runtime** — I only verified it doesn't send when told not to.
- **Connector auth configs / accounts / connect-requests.** Hard constraint (no credentials, no
  account create/delete). I used the pre-existing CONNECTED gmail account
  `019f893d-8d43-7100-bc72-06d51d36623d` throughout.
- **WEBHOOK schedules.** Would require creating a connector trigger against a live account; also needs
  a publicly reachable backend. Not attempted.
- **Surfaces** (Slack/Teams/Telegram/Gmail). Every surface needs a connector account and platform
  setup — out of scope under the credential constraint. `surfaces: 0` in the export.
- **Cross-org portability of the pinned-account grant.** Both pods are in the same org, so the
  `${support_triage_account}` default happened to resolve. The documented "hard failure when the org
  can't reach the account" path is untested — but note that with the current behaviour (**#6**) a
  cross-org import would *fail hard* rather than fall back, which is the opposite of what the docs
  promise.
- **`--as-template`.** Not exercised; budget.
- **The app in a browser.** `support-console` deployed to `READY` and the round-trip re-import
  succeeded, but I did not open it and click through it.
- **RLS behaviour.** All three tables are deliberately `enable_rls: false` (shared support queue), and
  I am the only member of both pods, so per-user row scoping was never exercised.
- **Whether F17 (`pod_write_record` empty payload) is a schema bug or model flakiness.** I observed it
  once, three times in a row, with a good server-side error each time. Attribution is a guess; the
  schema shape is the evidence.
