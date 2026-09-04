---
name: lemma-user
description: "Operate an existing Lemma pod from the CLI as a human or agent: inspect resources, query tables and records under RLS, search and read pod files (converted markdown, page images), run functions and workflows, submit waiting workflow forms, chat with pod agents, message pod members, and execute third-party connector operations. Do not use for designing or building pods; use lemma-builder instead."
---

# Lemma User

You are operating inside an **existing** pod — use its resources (tables, files,
functions, agents, workflows, connectors) to get work done for the user. You are
not redesigning the pod; that's the `lemma-builder` skill.

This is the operator companion to `lemma-builder`: the *runtime* view of the same
model. For the model itself, read `lemma-builder/references/pod-model.md` — this
doc grounds in it and assumes it.

## The model, from the operator's seat

(Grounds in `pod-model.md`.) A pod is one team's workspace under one permission
boundary. What that means when you run commands:

- **You act as a specific user.** Whether a human at a terminal or an agent on
  someone's behalf, every call carries **your identity**. A workload (function or
  agent) runs under **delegated identity** — it acts *as the user who invoked it*,
  never as a service account. So `/me` and row visibility always resolve to *that*
  user.
- **A workload's authority is the intersection**, not the union: its own grants
  **and** what the invoking person could do themselves. A grant is a ceiling on
  the workload, never a promotion for the person driving it — a `POD_VIEWER` who
  invokes an agent granted `datastore.record.write` still cannot write through
  it, and someone removed from the pod can do nothing through any workload. So
  "the agent is granted this" is only half an answer; check the person too.
- **RLS scopes what you see.** On an **RLS table** (the per-user default) you see
  and edit **only your own rows** — another member's row is invisible (a fetch
  returns `404`, a list omits it). On a **shared table** (`enable_rls: false`)
  everyone sees the same rows. This holds for *everyone*, admins included; reading
  across all users' rows needs an explicit `mode=ADMIN` opt-in (admin-gated, not
  the default flow). The read-only query API enforces RLS the same way.
- **`/me` is your private tree.** `/me/...` resolves to your own file subtree
  (owner-only). Every other path is **pod-shared** — top-level folders like
  `/knowledge`, `/contracts`. There is **no `/pod` prefix**: a path is shared
  unless it's under `/me`. Folder grants cascade to everything beneath them.
- **Missing access has three shapes**, and the refusal code says which. A human
  without the pod role gets `INSUFFICIENT_PERMISSION`. A **workload** missing a
  grant gets `MISSING_WORKLOAD_RESOURCE_GRANT`, naming the resource a builder
  must grant. A workload that *holds* the grant but is acting for someone who
  lacks the permission gets `DELEGATION_EXCEEDS_INVOKER` — granting the workload
  more is the one fix that cannot work; give the person the permission, or run
  it as someone who already has it.

Put user-facing deliverables in `/me` (or the appropriate shared folder) — never
leave the only copy in a local temp path.

## Orient first

```bash
lemma pods list            # marks the currently active pod
lemma pods describe        # inventory: tables, agents, functions, workflows, schedules + a file tree
                           # (apps are NOT in it — use `lemma apps list`)
                           # tree shows 2 folder levels; --depth N / --full for more
```

Workspace sessions inject `LEMMA_TOKEN`, `LEMMA_BASE_URL`, `LEMMA_ORG_ID`,
`LEMMA_POD_ID` (and `LEMMA_WORKSPACE_URL`) — use them; never invent bootstrap
config. (Outside an injected workspace — e.g. running the CLI on a laptop —
project-root `.lemma.<server>.env` files supply the same `LEMMA_*` values per
server for that folder; injected/real env always takes precedence.) Default output is a **compact,
complete** table/detail view (schemas
included) — prefer it; it costs far fewer tokens than JSON. Use `--output json`
(or `--json`) only to pipe/save, and `--full` to expand folded fields. **Errors,
warnings and progress lines go to stderr**, so stdout carries the result and
nothing else: `lemma --json … | jq` stays parseable even when the command fails,
and you never need to redirect to keep the JSON clean. Pass payloads with
`--data '<json>'` (`-d`) or `--file path.json` (`-f`); target another pod with
`--pod <id-or-slug>`; add `--yes` for destructive commands in automation. CLI
groups are plural (`lemma files`, `lemma tables`, `lemma records`, …), and most
have a singular alias (`lemma file`, `lemma table`). Not all: `query`,
`datastore`, `runtime`, `servers`, `telemetry`, `auth`, and `config` exist only
as written. There is no `lemma tools` group — first-party tools are the
in-process agent tools described below, not a CLI surface.

For multi-step scripting prefer the Python SDK over chained CLI calls:

```python
from lemma_sdk import Pod
pod = Pod.from_env()       # auth + pod from the environment
```

## Files — the pod is a searchable knowledge base

This is the area you'll lean on most. **Uploaded documents are auto-indexed —
the pod *is* the RAG system.** PDF/DOC/DOCX/ODT/RTF/Markdown/text/HTML/EPUB are
extracted, chunked, embedded, and converted to page-marked markdown on upload.
That allow-list is the whole of it: spreadsheets and tabular data (CSV, TSV,
JSON, YAML, XLSX, ODS), **presentations (PPTX, ODP)**, images (no OCR), email
(`.eml`/`.msg`), audio, video and archives are stored but **never indexed** —
they won't appear in search. So: **search to find, cat to read, child + view-image
to see.**

Because the pod auto-produces a document's markdown, page images, and figures, **read
those first** (the commands below) — never re-parse a pod file. Reach for the
`liteparse-documents` skill (`lit`) only for a document **from outside the pod** (e.g. a
PDF an agent fetched from the web) or as a **fallback** when a pod file's derived
artifact is missing or insufficient (scanned/OCR, bounding boxes).

### Search — find the relevant passages

```bash
lemma files search "refund policy" --scope /knowledge                 # HYBRID, folder + all subfolders
lemma files search "termination clause" --scope /contracts --method VECTOR   # semantic only
lemma files search "invoice 4471" --scope /inbox --method TEXT --direct      # keyword, immediate children only
```

Results are ranked passages **with page numbers**, so you can jump straight to
`cat … --pages N`. `--scope` + the default **SUBTREE** (folder and everything
beneath) is your retrieval lever — scope a search to one knowledge folder to keep
it tight. `--method` is `HYBRID` (default), `VECTOR` (semantic), or `TEXT`
(keyword); `--direct` limits to a folder's immediate children — it only takes
effect alongside `--scope`, and is ignored without one. Reach for search
before reading whole files or guessing.

### Read — `cat` is page- and mode-aware

```bash
lemma files cat /knowledge/handbook.pdf                 # auto: raw text for .md/.txt, converted markdown for PDF/DOCX/…
lemma files cat /knowledge/handbook.pdf --pages 3-7     # 1-based page slice over the converted markdown (great for long books)
lemma files cat /me/notes/log.md --lines 10-50          # 1-based line slice over raw text
lemma files cat /knowledge/handbook.pdf --mode markdown # force converted markdown (errors if not a document)
lemma files cat /scratch/data.csv --mode text           # raw bytes (binary → flagged, not dumped)
```

`--mode` is `auto` (default) / `text` / `markdown`. Output is capped at ~50,000
chars by default (matching the in-process agent tool); widen with `--max-chars 0`
(unlimited), `--max-lines N`, `--max-tokens N`, or `--full`, or narrow with
`--pages` / `--lines`. The payload reports `page_count`, the returned range, and a
`truncated` flag so you know when to page — page-range slicing is how you read a
long document without blowing the budget.

```bash
lemma files download /knowledge/handbook.pdf ./handbook.md --markdown   # save converted markdown
lemma files download /knowledge/handbook.pdf ./handbook.pdf             # exact original bytes
```

### See — child page images + view-image

A processed document exposes hidden child artifacts at `<file-path>/<artifact>`:

```bash
lemma files children /knowledge/handbook.pdf                          # list them
lemma files child /knowledge/handbook.pdf/document.md --pages 3-7     # page-marked markdown range
lemma files child /knowledge/handbook.pdf/pages/page_0003.jpg ./p3.jpg  # fetch a rendered page image
```

- `…/document.md` — page-marked converted markdown (`<!-- PAGE n -->`)
- `…/pages/page_0001.jpg` … — rendered page images (1-based)
- `…/images/image_0.png` … — extracted figures

**Use view-image to actually *see* a pod file.** Those rendered page JPEGs (and any
uploaded image) are exactly what the **view-image** capability reads — fetch one
with `files child` (or a URL with `files url`) and view it to see a chart, a
signature, a scanned form, a layout. So: "what does page 3 *look* like?" →
`files child …/pages/page_0003.jpg` → view-image; "what does it *say*?" →
`files cat … --pages 3`.

As an in-process tool, `view_image` takes exactly one of `pod_file_path` (the
datastore, no download needed) or `workspace_file_path` (the sandbox) — it never
infers the store from the path, and both or neither is an error. It handles
**images only**; hand it a PDF and it points you at `pod_view_document_pages`,
which renders that document's pages directly and skips the `files child` round
trip. It rides on any vision-capable model regardless of configured toolsets, so
you probably have it.

### Write & transfer

```bash
lemma files mkdir /knowledge
lemma files upload ./report.md /me/reports/report.md          # documents auto-index
lemma files upload ./data.csv /scratch/data.csv --no-search   # skip indexing
lemma files write /me/notes/draft.md "first line"             # create/overwrite (or pipe via stdin)
lemma files append /me/notes/draft.md "next line"             # append (read-modify-write, last writer wins)
lemma files ls /knowledge ; lemma files tree /
lemma files stat /knowledge/handbook.pdf                      # metadata incl. indexing status
lemma files mv /me/notes/draft.md /me/notes/final.md
lemma files rm /scratch/data.csv
```

Indexing lags briefly after upload — `stat` shows status (`COMPLETED` =
searchable, `NOT_REQUIRED` = stored but not an indexed document,
`PENDING`/`PROCESSING` = still working, `FAILED` = will be retried,
`FAILED_PERMANENT` = out of retries or unprocessable, and never re-driven; only a
fresh upload of the content re-opens it).

### Link to a file — pick by who opens it

```bash
lemma files url /reports/summary.pdf                           # app_url (in-app, signed-in member) + short-lived download url
lemma files share /reports/summary.pdf --ttl 3h --max-hits 50  # public, no-login, expiring + hit-capped
```

`url` returns an `app_url` deep-link for **pod members** (must be logged in) plus a
short-lived raw download `url`. `share` mints a **public** link anyone can open
without logging in — it expires (`--ttl` = `30m`/`3h`/`24h`; default 3h, max 24h)
and stops serving after `--max-hits` downloads (default 50, max 100), bounding
egress if it leaks. Emailing/messaging someone outside the pod → `share`; pointing
a member at a file in the app → `url`. (In a function or agent, the same via the
SDK: `pod.files.get_url(path)` / `pod.files.create_signed_url(path, …)`.)

## Tables, records, query

```bash
lemma tables list
lemma tables get tickets                              # schema: columns, types, enums
lemma records list tickets --limit 20
lemma records get tickets <record-id>
lemma records create tickets --data '{"title":"New item","status":"new"}'
lemma records update tickets <record-id> --data '{"status":"done"}'
lemma query run "select status, count(*) as total from tickets group by status"
```

Read the table schema before writing — **ENUM columns reject values outside
`options`**, and **a value over 256KB or a record over 1MB is refused** (put a
document in a pod file and keep its path in a `FILE_PATH` column). Prefer `query run` (a read-only SELECT subset — one SELECT, no writes)
for aggregates and joins instead of paging records; it reads across any tables,
including RLS tables, where it returns only your own rows (RLS scopes every caller
the same way). To read across all users' rows on an RLS table you'd pass
`mode=ADMIN` — admin-gated, not the default, and agents never use it.

**`query run` returns `{items, total, truncated}`, and `total` is not a count of
matches** — it is how many rows came back, equal to the match count only when
nothing was cut. Results stop at the deployment's row cap (1,000 by default);
past it `truncated` is `true` and `items` is a *prefix*. Read `truncated` before
you quote a number: a capped result looks exactly like a complete one, and
reporting `total` as a total is how you tell somebody they have 1,000 orders when
they have forty thousand. There is no cursor — aggregate in SQL (`count(*)`) or
narrow the query. (The in-process `pod_query` tool is the same shape under other
names: `rows`/`row_count`/`truncated`, plus a `note` when it was cut.)

## Functions, workflows, schedules

```bash
lemma functions list
lemma functions run score_ticket --data '{"ticket_id":"..."}'   # check output_data / status / logs
lemma functions runs list score_ticket                          # past runs (debug)
lemma functions runs get score_ticket <run-id>                   # NB: function AND run id

lemma workflows list
lemma workflows run intake --data '{"title":"..."}'             # WAITS for the run by default (--no-wait to fire);
                                                                # --data is submitted to the entry form
lemma workflows runs list intake
lemma workflows runs get <run-id>                               # status, current node, active_wait, step_history, errors
lemma workflows runs waiting                                    # form waits assigned to you (your approval queue)
lemma workflows runs submit-form <run-id> --data '{"approved": true}'  # complete the form the run is waiting on
lemma workflows runs cancel <run-id>                            # cancel a running/waiting run

lemma schedules list
lemma schedules pause <id> ; lemma schedules resume <id>
```

**`WAITING` on a run means a person, and only a person.** A run parked on an
agent conversation, an async function, or a timer stays `RUNNING` — so `RUNNING`
is not proof that anything is executing, and `WAITING` is not the general
"blocked" state. `runs get` settles it via `active_wait`: `wait_type` is `HUMAN`,
`AGENT`, `FUNCTION`, or `TIME`, alongside `node_id`, assignee, external
reference, and the form schema for human waits. If a form wait is assigned to you
(`runs waiting` lists them), `runs submit-form --data` with the form's fields
completes it and advances the run. This is how you participate in human-agent
workflows. A form with no assignee is submittable by any member with execute
access but belongs to nobody's queue, so an empty `runs waiting` does not mean
no run is parked.

A **conversation** in `WAITING` is a different thing, and the difference matters
before you go chasing it: it is either blocked on you (an `ask_user` question or an
approval card — answer it and the agent continues) or **snoozed**, meaning the agent
suspended itself and wakes on its own within 24 hours (the ceiling; requests above
it are clamped). A snoozed conversation is healthy and needs nothing from you.
The CLI does not distinguish the two — `conversations get` reports `status` and
`last_run_status` but no wait reason — so tell them apart from the transcript:
`conversations approvals <id>` lists an outstanding `ask_user`/approval, and an
empty list on a `WAITING` conversation means it is asleep.

A **notification** is the third thing, and unlike the other two it comes looking for
you: an agent or a workflow has asked *you* for something. They arrive wherever you
already talk to the pod — Slack, Telegram, WhatsApp, email — and always leave a copy
in your Lemma inbox, so nothing is only on a channel that can fail.

Each carries two independent states, and reading them as one is the usual mistake.
`status` is about you: `OPEN` (still owed), `RESPONDED`, `ACKNOWLEDGED` (seen and
dismissed, or sent as a pure FYI), `EXPIRED` (nobody answered in 72h),
`CANCELLED`. `delivery_status` is about the channel: `PENDING`, `DELIVERED`,
`UNDELIVERABLE` when no chat app or mailbox could carry it — usually because you
have never messaged the pod's bot — or `FAILED` when a channel *was* chosen and
the send raised. Only `FAILED` is a real delivery fault. `UNDELIVERABLE` is not:
the notification exists and the inbox has it.

Answer it in the app, or just reply on the surface it arrived on — the agent handling
that thread records your answer either way, and the asker sees the same result. A
notification with `responds_through_action` is a workflow form: it is answered by
`runs submit-form`, which validates against the node's schema, not by free text.
It resolves exactly once: a second answer, from another device or after an
expiry swept it, is refused rather than overwriting the first.

## Agents and chat

```bash
lemma chat "What can you do in this pod?"                           # one-shot to the DEFAULT pod agent
lemma chat                                                          # interactive, default pod agent
lemma agents list
lemma agents chat triage-agent "Summarize today's urgent tickets"   # named agent; interactive without a message
lemma agents run triage-agent "Classify this: ..."                  # waits + streams the ANSWER (--no-wait to detach)
lemma conversations list --agent triage-agent                       # an agent's runs (each run is a conversation)
lemma conversations list --parent-id <conversation-id>              # what a sub-agent was asked, and answered
lemma conversations messages <conversation-id>
lemma conversations send <conversation-id> "Continue with the next batch"
lemma conversations approvals <conversation-id>                     # gated tool calls / ask_user awaiting a decision
lemma conversations approve <approval-id> -c <conversation-id>      # --deny to reject; --session for the whole run
```

`lemma chat` takes an agent name, a message, or both, and tells them apart by
whitespace: one quoted multi-word argument is a **message** to the default pod
agent, one bare word is an **agent name** and opens an interactive session. Be
explicit with `--agent`/`--message` when a name could be mistaken for prose.

`conversations approve` with **no** approval id acts on *every* pending approval
in the conversation — it prints the list and asks you to confirm first, and
`--yes` skips even that. Name the id whenever you mean one decision.

An agent acts under your delegated identity — it sees exactly what you'd see (your
RLS rows, your `/me`, your connected accounts), and its grants are bounded by
your own access, never added to it (see "Missing access has three shapes").

### An agent can come and find a person

With the `MESSAGING` toolset an agent reaches a pod member who is **not** in the
conversation — that is where the notifications above come from. It is not a
paused wait: `message_user` returns immediately, the agent finishes its turn, and
it is given a *fresh* turn in the same conversation once the last person answers,
where `check_messages` reads the replies. Nothing sleeps and nothing polls.

- `list_pod_members` — resolve a name to an id or exact email (a name will not
  resolve on its own), and read `reachable_on`: the channels that can actually
  carry a message to that person right now.
- `message_user` — `to`, a Markdown `message`, an optional `background_instruction`
  (never shown to them; it tells the agent handling their reply what counts as an
  answer), an optional `title`, and an optional `channel` — `email`, `slack`,
  `teams`, `telegram`, `whatsapp`. Omit `channel` unless you have a reason: the
  default reaches them where they last spoke. A named channel is honored or
  refused, never quietly swapped, so check `reachable_on` first. Keep the returned
  `notification_id`.
- `check_messages` — status by notification id. `RESPONDED` is the only status
  that means somebody answered; call it when a turn opens saying replies landed,
  not in a loop.

To answer the person you are *already* talking to, just reply — `ask_user` is for
a question that must block this turn, and neither is `message_user`.

## Connectors

Two ways in, and which one you have depends on how the agent was granted:

- **Direct tools** (the `CONNECTORS` toolset) — in-process, no sandbox. These
  are *deferred*: they are not in your prompt prefix, so reach them with
  `search_tools` first, then `search_connector_operations` and
  `run_connector_operation`. Prefer this when you have it — no shell involved.
  (`CONNECTORS` is not alone behind `search_tools`: `POD`, `SUBAGENTS`,
  `MESSAGING` and `SNOOZE` are deferred the same way. Not seeing a tool in your
  prefix is not the same as not having it — go looking before concluding you
  cannot do something.)
- **The CLI** (`lemma connectors …`, needs the `WORKSPACE_CLI` toolset) — same
  operations through a sandbox round trip. Use it when you are driving a shell
  anyway, or when you need the discovery views below.

Either way the authorization is identical: a `connector:<name>:use` grant per
app, executed through the invoking user's connected account. Having the toolset
is not having access to any particular app.

### As direct tools

Once `search_tools` has surfaced them: leave `auth_config` unset and search by
what you want to do — the search spans every installed connector and each hit
names the `auth_config` to run it against, so you never have to guess which
install does email:

```text
search_connector_operations {"query": "send an email"}
  -> [{auth_config: "workspace-gmail", operation: "gmail_send_email", relevance_score: …}, …]

run_connector_operation {"auth_config": "workspace-gmail",
                         "operation": "gmail_send_email",
                         "arguments": {"recipient_email": "a@b.com", "subject": "Hi", "body": "…"}}
```

Wrong arguments come back as `invalid_arguments` **with the operation's
input_schema attached** — correct and retry, don't go fetch the schema
separately. Pass `auth_config` on search only to narrow to one install;
`describe_connector_operation` only when you want the full schema up front;
`output_path` on run to land a file result in the pod.

### From the CLI

Third-party connector operations — **`run` does the whole thing in one call**:
it resolves the connector, picks the operation, and executes. Still never guess a
payload; let `--dry-run` hand you the schema.

```bash
lemma connectors run gmail "list recent emails" --dry-run    # resolves + prints the input schema
lemma connectors run gmail gmail_list_messages -d '{"max_results": 5}'
lemma connectors run gmail gmail_send_email \
  -d '{"recipient_email": "a@b.com", "subject": "Hi", "body": "..."}'
```

The first argument is the **connector id** you already know from the task
(`gmail`, `slack`); it resolves to that connector's install. The second is an
operation id, or plain English — the resolved id is printed so you can name it
exactly next time. `--dry-run`, or simply omitting `--data` on an operation that
needs input, prints the input schema instead of failing. `--account` accepts an
account id or the connected email. `--metadata-only` strips large HTML body
fields from the response — reach for it when listing mail or documents, where
the bodies are most of the payload and none of the answer. An operation that
CHANGES data and was inferred from text rather than named is refused without
`--yes` — matching is lexical, so a read intent can land on a write.

When you want the wider picture rather than one call:

```bash
lemma connectors overview             # installed connectors: auth-config name, kind, connected accounts
lemma connectors status               # installed apps + your connected accounts
lemma connectors describe gmail       # per-connector usage guide, per kind
                                      # (kinds: package, composio, http, sql, mcp)
lemma connectors operations search "send email"                    # searches EVERY installed connector
lemma connectors operations search gmail "send email" --limit 5    # scoped; hits include their input schema
```

Workloads execute operations via the invoking user's connected account
(delegated) — they never touch raw credentials. If no account is connected, create
a connect request and hand the link to the user:
`lemma connectors connect-requests create gmail --auth-config-id <id>`.

## Workspace execution notes

- Long-running processes (dev servers, watchers, REPLs): keep one persistent
  interactive session and reuse it; one-shot shell commands for everything else.
- Local services are `http://127.0.0.1:<port>` inside the container. There is no
  user-constructible public preview URL for a workspace port — port access is a
  signed, expiring link the platform mints. To show someone a running app, deploy
  it (`lemma apps deploy`) rather than sharing a sandbox port.
- To keep web sources, use the `browser` skill's `save-webpage <url> --formats
  markdown,pdf`; upload durable artifacts to `/me` or a shared folder.
- Network errors (`Could not resolve host`, `ENOTFOUND`, TLS timeouts): check
  `curl -sS "$LEMMA_BASE_URL"` once, retry once, then report — don't loop.

## Troubleshooting

- **Row not visible / empty list / `404` on an RLS table.** You only ever see your
  **own** rows — an absent row usually belongs to another member, not a missing
  record. Confirm with the owner or, if you have the admin role and the feature
  warrants it, the `mode=ADMIN` read path. Don't assume data loss.
- **Permission denied / resource not visible.** Read the refusal code before you
  do anything. As a human you may lack the pod role — they ladder up:
  `POD_VIEWER` reads; `POD_USER` also writes records and runs
  agents/functions/workflows; `POD_EDITOR` also creates/updates tables and
  writes files; `POD_ADMIN` also deletes and manages members. As an agent,
  `MISSING_WORKLOAD_RESOURCE_GRANT` names a missing **workload grant** — a builder
  must add it (it never silently grants itself). `lemma pods doctor` lists every
  workload in the pod holding no grants at all, which is the usual cause; the fix
  is `lemma agents permissions add <name> <resource>:<perms>`. But
  `DELEGATION_EXCEEDS_INVOKER` means the opposite: the grant is there and the
  *person* is short. Adding grants will not move it — the person needs the
  permission, or the run needs a different invoker.
- **Resource not found.** Confirm the active pod (`lemma pods list`) and exact
  names (`lemma pods describe`; `lemma apps list` for apps).
- **"No such command" / "has no option".** You may be on an older CLI than the
  server. `lemma doctor` diagnoses version skew and duplicate installs;
  `lemma update` upgrades this CLI in place (`--version` to pin an exact one).
  Errors print on stderr, so a `--json … | jq` pipeline shows you an empty
  stdout rather than a parse failure — read the terminal, not just the pipe.
- **ENUM rejected on a record write.** Read `lemma tables get <table>` and use one
  of the listed `options`.
- **Fresh upload not in search.** Indexing lag — `files stat` for status, retry
  shortly. `NOT_REQUIRED` means it isn't an indexed document (spreadsheets,
  presentations, images and email are stored but never searchable);
  `FAILED_PERMANENT` means retrying will not help.
- **A query's numbers look too round.** Check `truncated` on the `query run`
  payload before believing `total` — 1,000 rows exactly is usually the row cap,
  not the answer.
- **Workflow stuck.** `runs get <run-id>` → `active_wait` shows what it's blocked
  on; `step_history` shows the failing node, its input, and error. Remember the
  run's own `status` is `RUNNING` for every wait except a human form, so
  `RUNNING` is not evidence of progress. A human wait needs `runs submit-form`.

## Report what got in your way

Hit a CLI, skill, or platform problem worth reporting — a confusing error, a
flag that didn't do what it says, information you had to discover by trial and
error, or something these skills got wrong? One command, and it is the only way
any of that gets fixed:

```bash
lemma feedback --category cli --subject "…" \
  --issue-encountered "…" --expected-behavior "…" --actual-behavior "…"
```

`--category` is one of `cli`, `skill`, `platform`, `docs`, `other` — which part
of Lemma the report is about.

## See also

- The model → `lemma-builder/references/pod-model.md`
- Build/restructure a pod → the `lemma-builder` skill
- Inline live views over pod data → the `lemma-widget` skill
- Drive a browser → the `browser` skill; test a pod app systematically → `lemma-app-qa`
- Run a source-backed investigation → the `lemma-research` skill
- Perform quantitative analysis → the `lemma-data-analysis` skill
- Package established content into a durable file → the `lemma-artifact-author` skill
- Local parsing/OCR of ad-hoc files → the `liteparse-documents` skill
