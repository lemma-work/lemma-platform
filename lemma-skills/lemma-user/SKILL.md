---
name: lemma-user
description: "Operate an existing Lemma pod from the CLI as a human or agent: inspect resources, query tables and records under RLS, search and read pod files (converted markdown, page images), run functions and workflows, submit waiting workflow forms, chat with pod agents, run first-party tools, and execute third-party connector operations. Do not use for designing or building pods; use lemma-builder instead."
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
- **Missing access has two shapes.** A human without the pod role gets a
  permission error; a **workload** missing a grant gets
  `MISSING_WORKLOAD_RESOURCE_GRANT` (naming the resource a builder must grant).

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
only to pipe/save, and `--full` to expand folded fields. Pass payloads with
`--data '<json>'` (`-d`) or `--file path.json` (`-f`); target another pod with
`--pod <id-or-slug>`; add `--yes` for destructive commands in automation. CLI
groups are plural (`lemma files`, `lemma tables`, `lemma records`, …), and most
have a singular alias (`lemma file`, `lemma table`). Not all: `tools`, `query`,
`datastore`, `runtime`, `servers`, `auth`, and `config` exist only as written.

For multi-step scripting prefer the Python SDK over chained CLI calls:

```python
from lemma_sdk import Pod
pod = Pod.from_env()       # auth + pod from the environment
```

## Files — the pod is a searchable knowledge base

This is the area you'll lean on most. **Uploaded documents are auto-indexed —
the pod *is* the RAG system.** PDF/DOC/DOCX/ODT/RTF/Markdown/text/HTML/EPUB are
extracted, chunked, embedded, and converted to page-marked markdown on upload.
Data/binary (CSV, JSON, XLSX, images, email) are stored but **never indexed** —
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
signature, a scanned form, a layout. This also works on **workspace** files
directly. So: "what does page 3 *look* like?" → `files child …/pages/page_0003.jpg`
→ view-image; "what does it *say*?" → `files cat … --pages 3`.

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
`PENDING`/`PROCESSING`/`FAILED`).

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
`options`**. Prefer `query run` (a read-only SELECT subset — one SELECT, no writes)
for aggregates and joins instead of paging records; it reads across any tables,
including RLS tables, where it returns only your own rows (RLS scopes every caller
the same way). To read across all users' rows on an RLS table you'd pass
`mode=ADMIN` — admin-gated, not the default, and agents never use it.

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

A run in `WAITING` is paused on a human form, an agent conversation, an async
function, or a timer — `runs get` shows which via `active_wait` (`wait_type`,
`node_id`, assignee, external reference, and the form schema for human waits). If a
form wait is assigned to you (`runs waiting` lists them), `runs submit-form --data`
with the form's fields completes it and advances the run. This is how you
participate in human-agent workflows.

A **conversation** in `WAITING` is a different thing, and the difference matters
before you go chasing it: it is either blocked on you (an `ask_user` question or an
approval card — answer it and the agent continues) or **snoozed**, meaning the agent
suspended itself and wakes on its own within 24 hours. A snoozed conversation is
healthy and needs nothing from you. Conversation status carries `wait_reason`
(`HUMAN` or `SNOOZE`) and, when snoozed, `wakes_at`.

## Agents and chat

```bash
lemma agents list
lemma agents chat triage-agent "Summarize today's urgent tickets"   # interactive or one-shot
lemma agents run triage-agent "Classify this: ..."                  # waits + streams the result (--no-wait to detach)
lemma conversations list --agent triage-agent                       # an agent's runs (each run is a conversation)
lemma conversations messages <conversation-id>
lemma conversations send <conversation-id> "Continue with the next batch"
```

An agent acts under your delegated identity — it sees exactly what you'd see (your
RLS rows, your `/me`, your connected accounts), plus only the resource grants its
builder gave it.

## Connectors

Hit a CLI, skill, or platform problem worth reporting? One command:

```bash
lemma feedback --category cli --subject "…" \
  --issue-encountered "…" --expected-behavior "…" --actual-behavior "…"
```

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
account id or the connected email. An operation that CHANGES data and was
inferred from text rather than named is refused without `--yes` — matching is
lexical, so a read intent can land on a write.

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
- **Permission denied / resource not visible.** As a human you may lack the pod
  role — they ladder up: `POD_VIEWER` reads; `POD_USER` also writes records and
  runs agents/functions/workflows; `POD_EDITOR` also creates/updates tables and
  writes files; `POD_ADMIN` also deletes and manages members. As an agent,
  `MISSING_WORKLOAD_RESOURCE_GRANT` names a missing **workload grant** — a builder
  must add it (it never silently grants itself). `lemma pods doctor` lists every
  workload in the pod holding no grants at all, which is the usual cause; the fix
  is `lemma agents permissions add <name> <resource>:<perms>`.
- **Resource not found.** Confirm the active pod (`lemma pods list`) and exact
  names (`lemma pods describe`; `lemma apps list` for apps).
- **ENUM rejected on a record write.** Read `lemma tables get <table>` and use one
  of the listed `options`.
- **Fresh upload not in search.** Indexing lag — `files stat` for status, retry
  shortly. `NOT_REQUIRED` means it isn't an indexed document (CSV/JSON/XLSX/images/
  email are stored but never searchable).
- **Workflow stuck.** `runs get <run-id>` → `active_wait` shows what it's blocked
  on; `step_history` shows the failing node, its input, and error. A human wait
  needs `runs submit-form`.

## See also

- The model → `lemma-builder/references/pod-model.md`
- Build/restructure a pod → the `lemma-builder` skill
- Inline live views over pod data → the `lemma-widget` skill
- Drive a browser → the `browser` skill; test a pod app systematically → `lemma-app-qa`
- Run a source-backed investigation → the `lemma-research` skill
- Perform quantitative analysis → the `lemma-data-analysis` skill
- Package established content into a durable file → the `lemma-artifact-author` skill
- Local parsing/OCR of ad-hoc files → the `liteparse-documents` skill
