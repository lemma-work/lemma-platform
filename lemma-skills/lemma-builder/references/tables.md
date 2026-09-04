# Tables And Records

Tables hold durable structured pod state. Design them first — every workflow, agent, schedule, and app reads or writes them. (Grounds in `pod-model.md` → the data layer.)

> Scaffold with `lemma tables init tickets [--shared]`. After import, add columns to a live table with `lemma tables add-column tickets priority --type INTEGER` and bulk-seed rows with `lemma records import tickets ./seed.csv` (CSV / JSONL / JSON).

## Table JSON

Bundle file `tables/tickets/tickets.json` (or `lemma tables create tickets --file ...`):

```json
{
  "name": "tickets",
  "primary_key_column": "id",
  "enable_rls": false,
  "columns": [
    { "name": "title", "type": "TEXT", "required": true, "max_length": 240 },
    { "name": "status", "type": "ENUM", "required": true, "default": "new",
      "options": ["new", "triaged", "waiting_approval", "resolved"] }
  ],
  "config": {}
}
```

### System-managed columns — never declare these

Every table automatically gets these, populated by Lemma. **Do not put them in `columns`** (and don't add them later with `add-column`):

- **`id`** — auto **UUID v7** primary key (added when `primary_key_column` is `id` and you didn't supply one). UUID v7 is **time-sortable**, so ordering by `id` ≈ insertion order — you rarely need a separate sequence for "newest first".
- **`created_at`, `updated_at`** — `DATETIME`, stamped by the backend: `created_at` on insert, `updated_at` on every insert **and** update. They are **not user-writable** — passing them in a create/update payload is ignored or rejected, and you can't model your own (use these for sorting, SLAs, "stale since", activity feeds).
- **`user_id`** — the row owner (`USER`), added **only when `enable_rls: true`** and auto-filled with the acting user's id on insert; you can't set it, and you can't set it to another user (see RLS below).

Declaring any of them is a hard error — e.g. `System-managed columns must not be declared explicitly: created_at`. Define only your own business columns; Lemma materializes the system ones.

## RLS vs shared tables

`enable_rls` decides who can see and touch each row, and it **defaults to `true`** — so a table is per-user-private unless you explicitly opt into sharing.

- **RLS table (`enable_rls: true`, the default)** — row-level security. Lemma adds a `user_id` column and **auto-fills it with the acting user's id** on insert (you never set it; you can't set it to another user). **Every caller sees only their own rows** — read, update, and delete are scoped to rows you own; another member's rows are invisible (a cross-user fetch returns `404`). This holds uniformly for everyone. Use RLS for **per-user/personal data**: each member's own tasks, drafts, settings, private notes.
- **Shared table (`enable_rls: false`)** — every member with record access sees and mutates **all** rows. Use this for **shared team data**: a support queue, a product catalog, reference data — anything the team works on together. You must set `enable_rls: false` explicitly; shared tables do not happen by default.

**Who is "the acting user" (and how agents fit in).** Every function/agent run executes under a **delegated identity** and is **owned by the user who invoked it**. So on an RLS table, a workload's inserts are stamped with that *invoking user's* id, and its reads return only that user's rows — exactly as if the user ran the query themselves. There is no separate "agent" owner.

**RLS is enforced everywhere, including the read-only query API.** `lemma query run` / the `/datastore/query` SQL endpoint runs under a non-superuser, `NOBYPASSRLS` database role with the caller's identity set, so the same row scoping applies to ad-hoc SQL. A React app calling the query API therefore shows each signed-in user **only their own rows** from RLS tables (and full shared-table rows) automatically — no per-user `WHERE user_id = ...` needed.

RLS scopes *which rows* a caller touches; it does **not** change *what permission a write needs*. Any record write requires `DATASTORE_RECORD_WRITE` (POD_USER and above) on both RLS and shared tables — see the role ladder in `pod-design.md`.

> Crossing the scope deliberately: a `mode=admin` query param on the query/record APIs returns **all** rows, skipping per-user scoping. It is gated on **table-admin permission** for every referenced table (not merely on holding the `POD_ADMIN` role), and a caller without it gets a `403` rather than a silently empty result. For admin/management features only; agents never use it.

## Data Types

| Type | Use for | Example |
| --- | --- | --- |
| `TEXT` | names, titles, descriptions, external ids | `"Refund request"` |
| `FILE_PATH` | references to Lemma files (shared `/…` or private `/me`) | `"/contracts/msa.pdf"` |
| `INTEGER` | counts, scores without decimals | `3` |
| `FLOAT` | amounts, confidence, quantities | `23.5` |
| `BOOLEAN` | flags | `true` |
| `JSON` | flexible payloads, model outputs, snapshots | `{"vendor": "Acme"}` |
| `DATE` | calendar date | `"2026-05-13"` |
| `DATETIME` | timestamp | `"2026-05-13T09:30:00Z"` |
| `UUID` | resource ids, stable references | `"4d06f8d1-..."` |
| `USER` | user identity references (owner, reviewer) | `"4d06f8d1-..."` |
| `VECTOR` | embeddings you generate + query yourself (semantic similarity over *structured* rows) | `[0.12, -0.31]` |
| `SERIAL` | auto-incrementing human-friendly numbers | `1001` |
| `ENUM` | lifecycle states, constrained categories | `"approved"` |

## Column Fields

- `required`, `unique`, `default`, `description`
- `foreign_key`: `{ "references": "other_table.id" }`
- `max_length` (TEXT / FILE_PATH) — characters, not bytes; independent of the value size limit below
- `options` — required for ENUM, only valid on ENUM
- `auto` — backend-generated; supported for INTEGER, SERIAL, UUID, USER
- `computed` + `expression` — SQL-computed column (cannot be required/auto/unique/FK)

## Sample Schemas

Work items (tickets, leads, incidents, requests):

```json
{ "name": "tickets", "enable_rls": false, "columns": [
  { "name": "number", "type": "SERIAL", "auto": true, "unique": true },
  { "name": "title", "type": "TEXT", "required": true, "max_length": 240 },
  { "name": "status", "type": "ENUM", "required": true, "default": "new",
    "options": ["new", "triage", "waiting", "done"] },
  { "name": "priority", "type": "ENUM", "default": "normal",
    "options": ["low", "normal", "high", "urgent"] },
  { "name": "owner_user_id", "type": "USER" },
  { "name": "source_payload", "type": "JSON" },
  { "name": "due_at", "type": "DATETIME" },
  { "name": "primary_file", "type": "FILE_PATH", "max_length": 500 }
]}
```

Child rows via foreign key (line items, comments, audit events):

```json
{ "name": "ticket_events", "enable_rls": false, "columns": [
  { "name": "ticket_id", "type": "UUID", "required": true,
    "foreign_key": { "references": "tickets.id" } },
  { "name": "kind", "type": "ENUM", "required": true,
    "options": ["created", "triaged", "approved", "resolved"] },
  { "name": "note", "type": "TEXT" }
]}
```

Documents under review (pairs with files — see `files.md`):

```json
{ "name": "documents", "enable_rls": false, "columns": [
  { "name": "title", "type": "TEXT", "required": true },
  { "name": "file_path", "type": "FILE_PATH", "required": true, "max_length": 700 },
  { "name": "kind", "type": "ENUM", "required": true,
    "options": ["contract", "invoice", "policy", "report"] },
  { "name": "extracted", "type": "JSON" },
  { "name": "review_status", "type": "ENUM", "default": "needed",
    "options": ["needed", "in_review", "approved"] }
]}
```

Per-user data (each member sees only their own rows) — keep RLS on (the default):

```json
{ "name": "my_preferences", "enable_rls": true, "columns": [
  { "name": "theme", "type": "ENUM", "default": "system",
    "options": ["system", "light", "dark"] },
  { "name": "digest_frequency", "type": "ENUM", "default": "daily",
    "options": ["off", "daily", "weekly"] }
]}
```

(The shared samples above set `enable_rls: false` because tickets, events, and documents-under-review are team data everyone works on; this one omits no field and stays RLS because preferences are private to each member.)

## Records

```bash
lemma records create tickets --data '{"title":"Refund request","status":"new"}'
lemma records list tickets --limit 20
lemma records get tickets <record-id>
lemma records update tickets <record-id> --data '{"status":"done"}'
lemma records create tickets --file payloads/ticket.record.json   # repeatable fixtures

lemma query run "select status, count(*) as total from tickets group by status"
```

`lemma query run` accepts a read-only SQL subset (a single SELECT; no writes) — use it for aggregates, joins, and verification instead of paging through records. It reads and joins **across any tables**, including RLS tables, whose rows are scoped to the caller (you only see your own).

A single create/update/delete is a **direct records-API call** like the ones above — don't wrap one write in a function. Reach for a function only when a write is **coordinated** with other writes, computation, or a connector call (`functions.md`, pod-model heuristic #6).

## Reacting to row changes

Two different needs, two mechanisms — don't conflate them (pod-model: server-side vs. client-side reaction):

- **Do work when a row changes** (triage the new ticket, notify, enrich) → a server-side `DATASTORE` **schedule** that starts an agent or workflow (`schedules-and-triggers.md`). This is also how one workload reacts to what another wrote — *reactive choreography*.
- **Keep an app's UI fresh when a row changes** → `datastore.watchChanges({ onChange })`, a client-side WebSocket the browser SDK exposes (`apps.md`). Never poll the table.

## Design Guidance

- Model the unit of work first; give it an ENUM status column — statuses drive workflows, app queues, and kanban views.
- `USER` columns for owner/reviewer/creator; store pod-member ids separately when a workflow form must be assigned to a specific member.
- `FILE_PATH` instead of copying document content into records. Files keep bytes + search index + converted markdown; records keep status, summaries, extracted fields.
- `JSON` for payloads you don't filter on yet; promote fields to typed columns when apps/queries need them.
- Split child collections into FK tables when they need their own lifecycle, permissions, or auditing.
- Store workflow run ids on records when users need to jump from business state to run history.
- `VECTOR` is for similarity over your *own* row data (e.g. dedupe leads, "similar tickets") where you produce and query the embeddings. For searching **documents**, don't hand-roll vectors — upload to files and use the built-in index (`files.md`).

## Limits & Gotchas

- **A value is capped at 256KB, and a whole record at 1MB.** Writes over either are refused, naming the column. Tables are for tabular data: the row is copied into every change event, so a megabyte column is a megabyte on every write, not once. Put documents in files and keep the path in a `FILE_PATH` column (`files.md`).
- Change events carry the row so subscribers can read a column the writer never mentioned. Above 32KB the body is dropped and the event is flagged `payload_truncated` — a subscriber that needs it reads the record.
- **No in-place column mutation** via import or update — columns can be added/removed only. Type changes = add new column, backfill, remove old.
- ENUM `options` changes are a column change; plan them like migrations if records exist.
- System columns are stripped from bundles on import; exporting then re-importing is safe.
- Functions/agents need explicit grants (`datastore.table.read`, `datastore.record.read`, `datastore.record.write`) per table — see `functions.md`.

## Verify

```bash
lemma pods import ./my-pod/tables/tickets --dry-run && lemma pods import ./my-pod/tables/tickets
lemma tables get tickets                       # confirm columns landed
lemma records create tickets --data '{"title":"smoke test","status":"new"}'
lemma query run "select count(*) from tickets"
```

## See also

- The model → `pod-model.md` · documents/RAG → `files.md`
- A table in an app (live refresh, RLS) → `app-recipes/rls-table.md`
- Roles & write permissions → `pod-design.md` · operate → the `lemma-user` skill
