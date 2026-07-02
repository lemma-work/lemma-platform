# Functions

Functions are typed Python entrypoints for **deterministic** pod logic: validation,
transformations, coordinated record writes, file handling, and third-party calls
through connectors. Use **agents** for judgment (classification, drafting); use
**functions** for work that must be predictable, testable, and auditable. A function
is the automation layer's "reliable verb" — the thing a workflow node, a schedule,
an app button, or an agent tool calls when the result must be the same every time.

**A function earns its place** when deterministic work spans **multiple steps** —
several writes at once, a write plus computation, or a third-party connector call (or a
mix). What does *not* belong in a function: a **single record write** (use the records
API directly — `lemma records create` / `pod.records.create(...)`) and **calling an
agent** (agents are first-class — call one directly, grant it as an `agent_<name>` tool,
or use a workflow AGENT node; never wrap it in a function). Reaching for the most direct
primitive keeps the work visible to grants and run history (pod-model heuristic #6).

> Grounds in `pod-model.md` (the automation layer). This is the build + CLI view;
> the `lemma-user` skill is the operator view of the same commands.

## The model, for functions

A function never runs "as itself." Two pod-model rules decide everything it can do:

- **Delegated identity.** A function runs **as the user who invoked it** — the
  sandbox is handed a *workload token* minted for that user, and `LEMMA_TOKEN` /
  `LEMMA_POD_ID` are injected so `Pod.from_env()` authenticates as that delegated
  principal. So RLS tables return only the **invoking user's** rows, inserts are
  stamped with **their** id, and `/me/...` resolves to **their** private tree. There
  is no workload-private space and no shared service account — a function sees exactly
  what the calling user would, never more.
- **Zero access by default.** A freshly created function can touch **nothing** — no
  tables, no folders, no connectors — regardless of what the human builder can see.
  Every resource the code touches needs an **explicit, name-based grant** in
  `permissions.grants` (a table name, a folder path like `/knowledge`, a connector
  id). Grants are **portable** (no UUIDs), travel in the bundle, and are **replaced**
  on every import. A missing one fails the run at the first access with
  `MISSING_WORKLOAD_RESOURCE_GRANT`, naming the resource.

Put together: a function is a narrow, granted capability exercised **on the invoking
user's behalf**. Design the input/output as a small typed contract; design the grants
as the exact set of resources it touches — nothing wider.

> Scaffold it: `lemma functions init save_expense` writes `save_expense.json` +
> `code.py` with the required `#…_type_name` headers; `lemma functions grant
> save_expense expenses:read,write` fills `permissions.grants`. Edit, then import.

## Anatomy

Bundle shape (folder name **must equal** the function `name`):

```text
my-pod/functions/save_expense/
  save_expense.json
  code.py
```

`save_expense.json` — note: **no input/output schemas here**, they are derived from
the code headers:

```json
{
  "name": "save_expense",
  "description": "Normalize and save an expense.",
  "type": "API",
  "code": {"$file": "code.py"}
}
```

`type: "API"` = synchronous request/response (the run blocks and returns the result).
`type: "JOB"` = long-running background work (the run is created, executes async; you
poll it). Use `API` for quick request/response and `JOB` for anything that may exceed
the request timeout.

`code.py` — the contract, in full:

```python
#input_type_name: SaveExpenseInput
#output_type_name: SaveExpenseResult
#function_name: save_expense

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class SaveExpenseInput(BaseModel):
    merchant: str
    amount: float

class SaveExpenseResult(BaseModel):
    record_id: str

async def save_expense(ctx: FunctionContext, data: SaveExpenseInput) -> SaveExpenseResult:
    pod = Pod.from_env()
    record = pod.table("expenses").create(
        {"merchant": data.merchant, "amount": data.amount, "status": "submitted"}
    )
    return SaveExpenseResult(record_id=str(record["id"]))
```

Rules:

- The header comment lines are **required and validated**: `#input_type_name`,
  `#output_type_name`, `#function_name` (must equal the resource/folder name), and
  `#config_type_name` when the code defines a config model. An optional
  `#python_packages` header (see below) declares pip dependencies. They must be the
  first lines of the file.
- Handler signature is `(ctx: FunctionContext, data: <InputModel>) -> <OutputModel>`,
  async or sync.
- `FunctionContext` fields: `ctx.pod_id`, `ctx.function_id`, `ctx.user_id`,
  `ctx.user_email` (the **invoking** user — your delegated identity), `ctx.config`.
- Keep input/output models small and JSON-serializable. Return ids and compact
  summaries, not big record lists.

## Python package dependencies

Common data packages — `numpy`, `pandas`, `matplotlib`, `openpyxl`, `pillow`,
`requests`, `tabulate` — are pre-installed. To use any other PyPI package, declare
it in a `#python_packages:` header line (with the other headers). The executor
installs declared packages before the function runs, so you can import them
normally:

```python
#input_type_name: ScrapeInput
#output_type_name: ScrapeResult
#function_name: scrape_page
#python_packages: beautifulsoup4, lxml

import bs4  # installed before the function runs
from pydantic import BaseModel, ...
```

- Each entry is a PyPI name with an optional `[extras]` and version specifier —
  e.g. `pandas`, `pandas==2.2`, `requests[socks]`, `numpy>=1.0,<2.0`. Entries are
  separated by spaces or commas. URLs, paths, pip flags, and spaces *inside* an
  entry are rejected.
- Packages install on the **first** run after a code change (then they're cached),
  so that first call is slower; for heavy packages, raise the caller's
  `timeout_seconds`. The install is shared with `execute_python` — no virtualenv to
  manage.

## The in-function SDK — `Pod.from_env()`

The sandbox has the `lemma_sdk` package installed and injects `LEMMA_TOKEN` (the
workload token carrying this function's grants, scoped to the invoking user) and
`LEMMA_POD_ID`. So:

```python
from lemma_sdk import Pod
pod = Pod.from_env()        # authenticated as the invoking user, with this function's grants
```

`Pod` exposes resource facades (all synchronous): `pod.records` / `pod.table(name)`,
`pod.files`, `pod.connectors`, `pod.workflows`, `pod.agents`, `pod.conversations`,
and `pod.query(sql)`. Single-record helpers return plain dicts; list/query helpers
return typed response objects — call `.to_dict()` on those to get plain data. Errors
raise `LemmaAPIError` with `.status_code`, `.message`, `.code`.

> **Read the SDK source when unsure.** The full Python SDK ships in the sandbox at
> **`/sdk/lemma-python`** (and the TypeScript SDK at `/sdk/lemma-typescript`). When
> you need an exact method signature, argument name, or response shape, read it
> directly instead of guessing or trial-running:
> ```bash
> cat /sdk/lemma-python/lemma_sdk/resources/data.py        # tables, records, queries
> cat /sdk/lemma-python/lemma_sdk/resources/files.py       # files
> cat /sdk/lemma-python/lemma_sdk/resources/connectors.py  # connector operations
> ls  /sdk/lemma-python/lemma_sdk/resources/               # every facade
> ```

### Response shapes (the #1 gotcha)

Response shapes differ by operation:

| Call | Returns | How to read it |
| --- | --- | --- |
| `records.create / get / update`, `table.create / get / update` | bare record dict | `record["id"]`, `record["status"]` |
| `records.list`, `table.list` | `RecordListResponse` | `.to_dict()["items"]` |
| `records.bulk_create / bulk_update / bulk_delete` | integer affected-row count | use directly |
| `pod.query(sql)` | `DatastoreQueryResponse` | `.to_dict()["items"]` |
| `connectors.execute(...)` | `OperationExecutionResponse` | `.to_dict()["result"]` |

### Tables and records — full CRUD

These reads/writes run **under the invoking user's RLS scope**: on an RLS table you
only see and write that user's rows; on a shared table you see the whole team's. See
`tables.md` for the RLS model.

```python
t = pod.table("tickets")                       # bound helper for one table

# create
row = t.create({"title": "Refund", "status": "new", "priority": "high"})
ticket_id = row["id"]

# read
row = t.get(ticket_id)

# update (only the fields you pass change)
t.update(ticket_id, {"status": "resolved"})

# delete
t.delete(ticket_id)

# list with filters + sort
rows = pod.records.list(
    "tickets", limit=50,
    filter=[
        {"field": "status", "op": "eq", "value": "new"},
        {"field": "priority", "op": "ne", "value": "low"},
    ],
    sort=[{"field": "created_at", "direction": "desc"}],
).to_dict()["items"]

# aggregate / join with raw read-only SQL (also RLS-scoped to the invoking user)
totals = pod.query(
    "select status, count(*) as total from tickets group by status"
).to_dict()["items"]
```

### Bulk record operations

Use these whenever you touch more than a couple of rows — one round-trip instead of N.

```python
# bulk create: list of row dicts (no id; ids are generated)
created_count = pod.records.bulk_create("ticket_events", [
    {"ticket_id": ticket_id, "kind": "created"},
    {"ticket_id": ticket_id, "kind": "triaged"},
])

# bulk update: each item is a FLAT dict that MUST include the primary key
updated_count = pod.records.bulk_update("tickets", [
    {"id": id_a, "status": "resolved"},
    {"id": id_b, "status": "waiting_approval", "priority": "urgent"},
])

# bulk delete: list of primary-key values
deleted_count = pod.records.bulk_delete("tickets", [id_a, id_b])
```

### Files

Pod files are **searchable by path** and **fully readable via converted markdown** —
`download_markdown` gives you the whole document (page-marked), `search` gives you
indexed chunks with page numbers, `download_child` fetches a rendered page image.
`/me` here is the **invoking user's** private tree. (Full file model: `files.md`.)

```python
hits = pod.files.search("refund policy")                        # indexed chunks (with pages)
md   = pod.files.download_markdown("/knowledge/policy.pdf")      # converted markdown bytes (page-marked)
pg   = pod.files.download_child("/knowledge/policy.pdf/pages/page_0003.jpg")  # one page image (bytes)
raw  = pod.files.download("/knowledge/policy.pdf")               # exact original bytes
pod.files.upload("/tmp/summary.md", directory_path="/reports", description="Weekly summary")
pod.files.write_text("/me/notes/draft.md", "first line")
```

Note the paths: shared files live at `/knowledge`, `/reports`, … (**no** `/pod`
prefix); personal files at `/me/...`. The grant `resource_name` is the stored path
**without any prefix** — `resource_name: "/knowledge"`.

#### File URLs (to put a link in an email, chat message, or record)

Two kinds — choose by **who opens the link**:

```python
# 1) Authenticated in-app link — for pod MEMBERS (they open it while signed in).
urls = pod.files.get_url("/reports/summary.pdf")
urls.app_url      # in-app file URL (permanent; opens for a signed-in member)
urls.url          # short-lived direct-download URL
urls.expires_at   # when urls.url stops working

# 2) Public signed link — for anyone OUTSIDE the pod (no login). Expiring + hit-capped
#    so a leaked link to a big file can't run up egress.
link = pod.files.create_signed_url("/reports/summary.pdf")                       # defaults: 3h, 50 downloads
link = pod.files.create_signed_url("/reports/summary.pdf",
                                   expires_seconds=86400, max_hits=5)            # 24h, 5 downloads
link.signed_url   # https://<api>/s/<code>  — short, copy-pasteable
link.expires_at
link.max_hits     # effective cap, clamped server-side (max 24h / 100 hits)
```

Rule of thumb: **pod member → `get_url().app_url`; external recipient →
`create_signed_url().signed_url`.** Never paste raw file bytes or an internal storage
path into a message.

### Connector operations (calling external apps)

`pod.connectors.execute(auth_config, operation, payload)` runs a third-party
operation **through the invoking user's connected account** (delegated) — the
function never touches raw credentials. Discover the exact operation id and payload
from the CLI first (`lemma connectors operations search/details …` — see
`connectors.md`); the payload shape is operation-specific. The response is
`{"result": …}` — unwrap with `.to_dict()["result"]`.

```python
# Send an email via Gmail (then send a public file link from above)
sent = pod.connectors.execute(
    "workspace-gmail",                  # the AUTH CONFIG name (not the bare connector id)
    "GMAIL_SEND_EMAIL",                 # operation id from `operations search`
    {
        "recipient_email": data.to,
        "subject": "Your report is ready",
        "body": f"Download (link expires in 24h):\n{link.signed_url}",
    },
).to_dict()["result"]

# List the next calendar events
events = pod.connectors.execute(
    "workspace-gcal",
    "GOOGLECALENDAR_EVENTS_LIST",
    {"calendarId": "primary", "maxResults": 10, "singleEvents": True, "orderBy": "startTime"},
).to_dict()["result"]
```

Operation ids and payload keys differ between the `LEMMA` and `COMPOSIO` providers —
always confirm with `operations details` for the provider your org installed. Don't
resolve or pass `account_id` in code unless you must pin a specific account: the
backend selects the configured fixed account or the **invoking user's** connected
account from the workload token. The connector must be granted to the function
(`resource_type: "connector"` — see Permissions below).

### Workflows, agents, conversations

```python
run = pod.workflows.create_run("ticket-intake")
if run.active_wait and run.active_wait.wait_type == "HUMAN":
    run = pod.workflows.submit_form(str(run.id), node_id=run.active_wait.node_id, inputs={"ticket_id": rid})
conv = pod.conversations.create_for_agent("triage-agent", title="Triage")
pod.conversations.send(str(conv.id), "Classify ticket " + rid)
```

## Permissions (workload grants)

**A newly created function can access nothing** — zero default access, no matter what
the builder can see. Every resource the code touches must be granted explicitly, or
the run fails at the first access. Grants are **name-based** (reference resources by
the names you already use — no UUID copying) and **portable** (they resolve against
whatever pod you import into):

```json
{
  "grants": [
    { "resource_type": "datastore_table", "resource_name": "expenses",
      "permission_ids": ["datastore.table.read", "datastore.record.read", "datastore.record.write"] },
    { "resource_type": "folder", "resource_name": "/reports",
      "permission_ids": ["folder.read", "folder.write"] },
    { "resource_type": "connector", "resource_name": "gmail",
      "permission_ids": ["connector.use"] }
  ]
}
```

`resource_name` per type:

| `resource_type` | `resource_name` is… | example |
| --- | --- | --- |
| `datastore_table` | the table name | `expenses` |
| `folder` | the **stored folder path, no prefix** | `/reports`, `/knowledge` |
| `connector` | the connector id | `gmail` |

> **File grants take the bare path.** The grant `resource_name` for a folder is the
> path as stored — `/knowledge`, `/reports` — with **no** `/pod` (or any) prefix.
> Folder grants **cascade**: granting `/knowledge` covers every file and subfolder
> beneath it. `/me` is the invoking user's own tree and needs no grant.

Grants live in the bundle: `lemma pods export` embeds each function's current grants
under `permissions.grants`, and `lemma pods import` **replaces** the function's grants
with that list on every upsert (create and update). The bundle is the source of
truth — removing a grant from the JSON and re-importing revokes it. You can also
manage grants directly:

```bash
lemma functions grant save_expense expenses:read,write /reports:read,write connector:gmail:use
lemma functions permissions replace save_expense --file payloads/save_expense.permissions.json
lemma functions permissions get save_expense
```

A run failing with `MISSING_WORKLOAD_RESOURCE_GRANT` names the resource it tried to
reach — add exactly that grant and retry.

### Exposing a function as an agent's tool

Grants also flow the other way: grant an **agent** `function.execute` on a function
(`resource_type: "function"`, `resource_name: <function-name>`) and the function
becomes a callable tool (`function_<name>`) for that agent, with the function's input
schema as the tool arguments. This is the clean way to give an agent deterministic,
auditable capabilities mid-conversation.

**A function tool needs exactly one grant.** `function.execute` on the parent implies
`function.read`, so that single grant covers both discovering and running the tool.
The function runs under **its own** FUNCTION principal with **its own** grants — the
same identity it has when run directly or as a job — so you grant the tables / files /
connectors it touches to the **function**, never mirrored onto the parent. A
`MISSING_WORKLOAD_RESOURCE_GRANT` from a tool call names the resource the *function*
lacks; fix it on the function.

Use the shorthand or the bundle JSON:

```bash
lemma agents grant <parent> function:<fn>:execute
```
```jsonc
{ "resource_type": "function", "resource_name": "<fn>",
  "permission_ids": ["function.execute"] }
```

See `agents.md` → "Agents & Functions as Tools" and `authorization-model.md` §6 for
the full model.

## Config

For durable settings (thresholds, target folders, operation names), define a config
model and header:

```python
#config_type_name: SaveExpenseConfig

class SaveExpenseConfig(BaseModel):
    default_status: str = "submitted"

# in the handler:
status = ctx.config.default_status if ctx.config else "submitted"
```

Secrets belong in connected connector accounts, **never** in function config or code.

## Patterns

**Validate-then-write (the reliable verb).** A workflow's FUNCTION node maps fields in;
the function validates, normalizes, and does a coordinated multi-row write under the
invoking user's identity, returning ids. Keep all the "must be exact" logic here, out
of the agent.

```python
async def record_approval(ctx, data: RecordApprovalInput) -> RecordApprovalResult:
    pod = Pod.from_env()
    pod.table("requests").update(data.request_id, {"status": "approved" if data.approved else "rejected"})
    pod.records.bulk_create("request_events", [
        {"request_id": data.request_id, "kind": "decided", "approved": data.approved},
    ])
    return RecordApprovalResult(request_id=data.request_id)
```

**Extract-from-document.** Read the converted markdown of a granted file, parse, write
structured fields back to a record — the "files → tables" handoff (`files.md`). Grant
the folder `folder.read` and the table `datastore.record.write`.

**Send a report link.** Generate a file, then a *public* signed URL, then email it via
a granted connector — see the connector example above. Grant the folder and the
connector.

**Deterministic agent tool.** Expose the function to a coordinator agent so the LLM
can call it mid-conversation for an auditable write — see "Exposing a function as an
agent's tool" and `agents.md`.

## Test loop

```bash
lemma pods import ./my-pod/functions/save_expense --dry-run
lemma pods import ./my-pod/functions/save_expense
lemma functions run save_expense --data '{"merchant":"Delta","amount":420.0}'
lemma functions run save_expense --file payloads/save_expense.input.json
lemma --output json functions run save_expense --data '{...}'   # parse output_data/status/logs

# API-type runs block and return the result; JOB-type runs are async — start
# with --no-wait, then inspect past runs while debugging:
lemma functions runs list save_expense          # recent runs (latest first)
lemma functions runs get save_expense <run-id>  # status, input, output, logs, error
```

Failed runs return `error` and `logs` (stdout/stderr) on the run object. Iterate by
editing `code.py` and re-importing — code updates are cheap. `functions runs list/get`
make past runs inspectable, so debugging is not a black box.

## Limits & gotchas

- **Schemas are immutable after create.** Changing the Pydantic models in code does
  **not** update `input_schema`/`output_schema` on upsert. Delete and recreate the
  function (or version the name) for schema changes.
- **Zero default access.** A function with code but no grants fails at the first
  table/file/connector touch with `MISSING_WORKLOAD_RESOURCE_GRANT` — grant exactly
  what the named resource asks for. Grants are replaced wholesale on import.
- **Delegated, not elevated.** A function cannot see another user's RLS rows or
  another user's `/me` — it runs as the invoking user. If you need cross-user reads,
  that's an admin path (`mode=ADMIN` on a query, table-admin permission), not a
  function default.
- `#function_name` must match the resource/folder name or import fails.
- Python syntax is parsed at import; imports beyond the pre-installed or declared
  packages fail at execution — declare extra deps via `#python_packages` (see
  *Python package dependencies*).
- API-type functions have a request/response timeout — use `type: "JOB"` for anything
  long-running.
- Test both happy and failure paths before wiring the function into a workflow.

## Verify

```bash
lemma functions run save_expense --data '{"merchant":"Test","amount":1.0}'
lemma records list expenses --limit 3          # confirm the write actually happened
lemma functions permissions get save_expense   # grants present
lemma functions runs get save_expense <run-id> # logs/error on a failure
```

## See also

- The model → `pod-model.md` · structured data the function reads/writes → `tables.md`
- Documents/RAG the function reads → `files.md` · external apps it calls →
  `connectors.md`
- Calling a function from an agent → `agents.md` · from a graph → `workflows.md`
- Operate an existing pod's functions → the `lemma-user` skill
