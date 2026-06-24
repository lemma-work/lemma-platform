# Lemma Python SDK

`lemma-sdk` is the Python client library for Lemma. It wraps a generated OpenAPI
client with a pod-first, ergonomic surface for tables, files, functions, agents,
workflows, schedules, surfaces, desks, and connectors.

It is the same SDK that runs **inside Lemma functions** (where the runtime injects
auth automatically) and in **standalone application code** (where you supply a
token). The CLI and TUI live in the sibling `lemma-cli` package.

- package name: `lemma-sdk`
- import root: `lemma_sdk`
- Python `>=3.11` ([`uv`](https://docs.astral.sh/uv/) recommended)

> **Reading the source.** In a Lemma sandbox the full SDK source is available at
> `/sdk/lemma-python` (and the TypeScript SDK at `/sdk/lemma-typescript`). When you
> need an exact signature or response shape, read it rather than guessing —
> e.g. `cat /sdk/lemma-python/lemma_sdk/resources/data.py`.

## Install

```bash
uv pip install .            # or: uv pip install --editable .
python -c "from lemma_sdk import Pod, Lemma; print(Pod, Lemma)"
```

## Two entry points

| Class | Scope | Use for |
| --- | --- | --- |
| `Pod` | one pod | almost everything — data, files, functions, agents, workflows, app operations |
| `Lemma` | org / global | org & pod discovery, org-level connector setup, tools, runtime profiles |

```python
from lemma_sdk import Pod, Lemma

pod = Pod.from_env()                    # token + pod id from env / CLI session
lemma = Lemma.from_env(org_id="org-id") # org-scoped client; lemma.pod("id") -> Pod
```

`Pod` is a context manager and owns an HTTP transport when constructed directly:

```python
with Pod.from_env() as pod:
    pod.functions.run("triage_ticket", {"ticket_id": "rec-1"})
```

## Authentication & configuration

The SDK resolves settings from explicit arguments first, then environment, then
the CLI config file (`~/.lemma/config.json`).

Environment variables:

```bash
export LEMMA_TOKEN="<access-token>"      # required if not using a CLI session
export LEMMA_POD_ID="<pod-id>"           # required for Pod.from_env()
export LEMMA_ORG_ID="<org-id>"           # required for org-scoped calls
export LEMMA_BASE_URL="https://api.lemma.work"
export LEMMA_AUTH_URL="https://lemma.work/auth"
export LEMMA_REFRESH_TOKEN="<refresh-token>"     # optional
export LEMMA_CONFIG_FILE="~/.lemma/config.json"  # optional override
export LEMMA_SSL_NO_VERIFY=1              # local/self-signed only
```

Inside a Lemma function, `LEMMA_TOKEN` (a workload token scoped to the function's
grants) and `LEMMA_POD_ID` are injected for you — just call `Pod.from_env()`.

Explicit construction (no env needed):

```python
pod = Pod(pod_id="pod-id", org_id="org-id", token="token",
          base_url="https://api.lemma.work")
```

When `LEMMA_TOKEN` is unset, settings fall back to the selected server in the CLI
config:

```json
{
  "active_server": "cloud",
  "servers": {
    "cloud": {
      "base_url": "https://api.lemma.work",
      "auth_url": "https://lemma.work/auth",
      "defaults": { "org_id": "org-id", "pod_id": "pod-id" }
    }
  }
}
```

(Legacy `active_context` / `contexts` keys are still accepted and translated.)
Install and manage the CLI from `lemma-cli`; see `lemma-cli/SETUP.md`.

## Response shapes — read this first

Every method returns a typed response object. Call `.to_dict()` for plain data,
then unwrap:

| Call | `.to_dict()` returns | Rows under |
| --- | --- | --- |
| `records.create / get / update` | the **bare record object** (no envelope) | top-level |
| `table.create / get / update` | the table detail object | top-level |
| `records.list`, `table.list` | `{"items": [...], "total": N, "limit": N, "next_page_token": ...}` | `["items"]` |
| `bulk_create / bulk_update / bulk_delete` | `{"count": N}` | `["count"]` |
| `query(sql)` | `{"items": [...], "total": N}` | `["items"]` |
| `connectors.execute` | `{"result": ...}` | `["result"]` |
| `functions.run` | `{"status": ..., "output_data": ..., "logs": ...}` | top-level |

Single-record create/get/update return the record directly — there is **no**
`{"data": {...}}` envelope. Call `.to_dict()` and use the result as the row.
The `pod.records` helpers already unwrap to a plain dict for you. The bulk
helpers return the integer `count` directly.

## Pod facades

`pod.tables` · `pod.records` · `pod.queries` · `pod.files` · `pod.functions` ·
`pod.agents` · `pod.workflows` · `pod.schedules` · `pod.conversations` ·
`pod.members` · `pod.apps` · `pod.surfaces` · `pod.connectors`

Plus helpers: `pod.table(name)` (bound single-table helper), `pod.query(sql)`,
`pod.generated` (raw OpenAPI client escape hatch).

### Tables & records — full CRUD

```python
t = pod.table("tickets")

row = t.create({"title": "Refund", "status": "new"})   # already a plain dict
ticket_id = row["id"]

row = t.get(ticket_id)                            # bare record dict, no envelope
t.update(ticket_id, {"status": "resolved"})       # only passed fields change
t.delete(ticket_id)

rows = pod.records.list(
    "tickets", limit=50,
    filter=[
        {"field": "status", "op": "eq", "value": "new"},
        {"field": "priority", "op": "ne", "value": "low"},
    ],
    sort=[{"field": "created_at", "direction": "desc"}],
).to_dict()["items"]

totals = pod.query(
    "select status, count(*) as total from tickets group by status"
).to_dict()["items"]
```

The `pod.records` / `pod.table(...)` create/get/update helpers return the bare
record as a plain dict (no `.to_dict()`, no `["data"]` unwrap). `list` and
`query` return response objects; call `.to_dict()` and read `["items"]`.

Record data is dynamic because table schemas are user-defined.

#### RLS vs shared tables

Tables carry an `enable_rls` flag that **defaults to `true`** (row-level
security on). With RLS on, each row is owned by its creator: non-admin members
read/update/delete **only their own rows** (other users' rows are invisible —
cross-user access returns 404), while pod admins see and manage every row. This
is the right default for per-user/personal data.

Set `enable_rls: false` for SHARED/reference/team tables that all members should
see and mutate. RLS only scopes *which* rows a non-admin can touch — it does not
change the permission a write needs: writing any table requires the
`DATASTORE_RECORD_WRITE` permission (POD_USER and above), RLS or not. The
read-only `query` endpoint can join across tables only when they are non-RLS.

### Bulk record operations

```python
# create: row dicts (ids generated)
created_count = pod.records.bulk_create("ticket_events", [
    {"ticket_id": ticket_id, "kind": "created"},
    {"ticket_id": ticket_id, "kind": "triaged"},
])

# update: FLAT dicts that MUST include the primary key
updated_count = pod.records.bulk_update("tickets", [
    {"id": id_a, "status": "resolved"},
    {"id": id_b, "status": "waiting", "priority": "urgent"},
])

# delete: list of primary-key values
deleted_count = pod.records.bulk_delete("tickets", [id_a, id_b])
```

### Files — searchable documents (built-in RAG)

Files uploaded to a pod are **automatically indexed**: text is extracted,
chunked, and embedded, so they become searchable with no separate vector DB or
infra. That makes files the pod's built-in retrieval-augmented generation store.

Only **document** formats are indexed: PDF, DOC/DOCX, ODT, RTF, Markdown, plain
text, HTML, EPUB. Data/binary formats (CSV, TSV, JSON, YAML, XLSX, images,
email) are stored but **not** indexed (status `NOT_REQUIRED`) and never appear in
search — so keep structured data in **tables** and prose/documents in **files**.
`search_enabled` toggles indexing per file; status flows
PENDING → PROCESSING → COMPLETED (searchable) / NOT_REQUIRED / FAILED, and only
COMPLETED documents are searchable. Documents are also converted to markdown.

`/me` is each user's **private** per-user tree (only the owner sees their `/me`
files); all other paths are pod-shared, and folder grants cascade to every
descendant.

```python
pod.files.create_folder("/reports", description="Generated reports")
pod.files.upload("/tmp/summary.md", directory_path="/reports")

# Plain search (defaults to the whole pod):
hits = pod.files.search("refund policy").to_dict()

# Directory-scoped RAG + method selection:
hits = pod.files.search(
    "refund policy",
    scope_path="/knowledge",     # restrict to a folder
    scope_mode="SUBTREE",        # SUBTREE = folder + all descendants (default); DIRECT = immediate children only
    search_method="HYBRID",      # TEXT (full-text), VECTOR (semantic), or HYBRID
).to_dict()

md  = pod.files.download_markdown("/knowledge/policy.pdf")           # converted markdown bytes
kids = pod.files.list_children("/knowledge/policy.pdf")              # derived child files (md, figures, pages)
raw = pod.files.download("/knowledge/policy.pdf")                     # bytes
```

### Functions, agents, workflows

```python
run = pod.functions.run("triage_ticket", {"ticket_id": "rec-1"}).to_dict()
# run["status"], run["output_data"], run["logs"]

agent = pod.agents.get("triage").to_dict()
conv = pod.conversations.create_for_agent("triage", title="Triage")
pod.conversations.send(str(conv.to_dict()["id"]), "Classify ticket rec-1")

wf_run = pod.workflows.create_run("nightly_review").to_dict()
# Workflow inputs are collected by FORM nodes mid-run, not at start; submit them with
# pod.workflows.submit_form(wf_run["id"], node_id="<form_node>", inputs={"limit": 10})
```

### Connectors (calling external apps)

`pod.connectors.execute(auth_config, operation, payload)` runs a third-party
operation. The first argument is the **auth config name** (often the app id), the
operation id and payload come from discovery, and the response is under
`["result"]`.

```python
sent = pod.connectors.execute(
    "workspace-gmail",          # auth config name
    "GMAIL_SEND_EMAIL",         # operation id from discovery
    {"recipient_email": "a@example.com", "subject": "Hi", "body": "..."},
).to_dict()["result"]

# discover before you call:
matches = pod.connectors.operations.search("workspace-gmail", "send email")
schema  = pod.connectors.operations.get("workspace-gmail", "GMAIL_SEND_EMAIL")
```

Operation ids and payload keys differ between the `lemma` and `composio`
providers — confirm with discovery for the provider your org installed. Don't pass
`account_id` unless pinning a specific account; the backend resolves the fixed or
invoking-user account from the token.

## Org & global usage (`Lemma`)

```python
lemma = Lemma.from_env(org_id="org-id")

org   = lemma.org.get()
pods  = lemma.pods.list()
pod   = lemma.pod("pod-id")          # -> Pod sharing this transport
me    = lemma.user.profile()

# org-level connector setup
auth_configs = lemma.connectors.auth_configs.list()
accounts     = lemma.connectors.accounts.list(app="gmail")

# first-party tools
results = lemma.tools.web_search("vendor SLA policy", max_results=5)
```

Facades: `lemma.orgs` · `lemma.org` · `lemma.pods` · `lemma.user` ·
`lemma.connectors` · `lemma.tools` · `lemma.runtime` · `lemma.org_runtime`.

## Writing a function

A Lemma function is a Python file with header comments declaring its types, plus a
handler `(ctx, data) -> output`:

```python
#input_type_name: TriageInput
#output_type_name: TriageResult
#function_name: triage_ticket

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class TriageInput(BaseModel):
    ticket_id: str

class TriageResult(BaseModel):
    status: str

async def triage_ticket(ctx: FunctionContext, data: TriageInput) -> TriageResult:
    pod = Pod.from_env()    # authenticated as this function's workload principal
    pod.table("tickets").update(data.ticket_id, {"status": "triaged"})
    return TriageResult(status="triaged")
```

`FunctionContext` fields: `pod_id`, `function_id`, `user_id`, `user_email`,
`config`. The function runs with **zero default access** — grant it the tables,
folders, and apps it touches (see the `lemma-builder` skill / `lemma functions
permissions`).

## Typed models

`lemma_sdk.models` re-exports the common response types with friendly names:

```python
from lemma_sdk.models import Record, FunctionRun, OperationExecution, Agent, Function

record: Record = pod.records.create("tickets", {"title": "Typed"})
```

Generated request models live under `lemma_sdk.openapi_client.models` (e.g.
`CreateTableRequest`) for endpoints you build payloads for by hand.

## Errors

```python
from lemma_sdk import LemmaAPIError, LemmaConfigError

try:
    pod.records.get("tickets", "missing")
except LemmaAPIError as e:
    print(e.status_code, e.code, e.message)
except LemmaConfigError:
    ...   # missing token / pod id / unreadable config
```

## Generated client escape hatch

For endpoints not yet wrapped by the ergonomic SDK:

```python
generated = pod.generated   # authenticated; same base URL/token/timeout/SSL
```

## Testing against a real Lemma API

Unit tests cover wrapper behavior; an opt-in integration scenario runs real
end-to-end work against a running API. Start the local stack from the repo root:

```bash
make dev
```

Then from `lemma-python`:

```bash
export LEMMA_TOKEN="<access-token>"
LEMMA_RUN_CONNECTOR=1 uv run --with pytest --with pytest-asyncio \
  pytest tests/integration -m integration -s
```

It defaults to `http://127.0.0.1:8711` (the local API) and falls back to the CLI
auth session if `LEMMA_TOKEN` is unset. Point elsewhere with
`LEMMA_CONNECTOR_BASE_URL` / `LEMMA_CONNECTOR_TOKEN`. The scenario creates a
fresh org and pod, exercises tables/records/query/files/functions/agents/
workflows/connectors, prints a summary, and deletes the pod.

## Regenerate the SDK

Run the backend locally, then regenerate from its OpenAPI spec:

```bash
bash scripts/generate_openapi_client.sh
OPENAPI_URL=http://127.0.0.1:8000/openapi.json OPENAPI_INSECURE=1 \
  bash scripts/generate_openapi_client.sh
```

Generator env vars: `LEMMA_API_URL`, `OPENAPI_URL`, `OPENAPI_INSECURE`,
`LEMMA_SSL_NO_VERIFY`.

## Development checks

```bash
uv run ruff check lemma_sdk tests
uv run --with pytest python -m pytest tests
```
