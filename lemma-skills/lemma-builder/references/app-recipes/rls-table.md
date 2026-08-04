# Recipe — a table with live auto-refresh

Read and mutate a table; a local write auto-refreshes the list, and a WebSocket keeps it
live across *other* users' / workloads' changes — no polling. (← `apps.md`)

## What RLS does here

The app runs as the signed-in user (see `pod-model.md`). On an **RLS-on** table the
hooks return **only that user's rows**; on a **shared** table they return the
team's rows. You never filter by `user_id` — the backend scopes it.

## A write can start agentic work — as the writer

This is the reason RLS tables are worth reaching for in an app. When a
**`DATASTORE`** schedule watches an RLS table, the run it starts belongs to the
**owner of the row that changed**, not to whoever created the schedule
(`datastore_event_handler.py`). So an app row insert becomes: *this user asked
for this, and the work runs with exactly their reach.*

```
app writes a row (as the signed-in user)
      → DATASTORE schedule fires, carrying that row's owner
      → workflow/agent runs as that user, seeing only their RLS rows
      → result lands in a table the app is already watching
```

The app side is just a write plus the live hooks below — no polling, no job
status endpoint. What you have to get right is the trigger:

- **The workflow's `start` block does not create the trigger.**
  `start: {type: "DATASTORE_EVENT"}` is a declaration; firing needs a separate
  **`Schedule`** row with `schedule_type: DATASTORE`, the `table_name`, and
  `operations`. Create both.
- **`operations` is mandatory on the schedule** (`["create"]`, `["update"]`, …).
  It is optional on the workflow `start` model and defaults to "all" there —
  that default does not carry over, and a schedule without it matches nothing.
- **Creating one needs `datastore.table.update` on the watched table**, not just
  record-write. A `POD_USER` who can add rows may still not be able to create the
  trigger.
- **On a shared (non-RLS) table there is no row owner**, so the run falls back to
  the schedule's creator — every member's write runs as *that* person. If you
  want per-user scoping, the table must be RLS-on.
- **There is no loop guard.** An agent writing back into the *watched* table
  re-fires the trigger. Write results to a **different** table.
- **Grant the agent, not the node.** The workflow's AGENT node does not check
  `agent.execute`; what decides whether the agent can write the result table is
  the agent's own resource grants.

The app then reads results with `useLiveRecords` on the results table, and rows
appear as the workflow finishes.

## Read + filter + sort (hand-written hook)

```tsx
import { useRecords } from "lemma-sdk/react";

const { records, isLoading, error, loadMore, hasMore } = useRecords({
  client, podId: client.podId, tableName: "tickets", limit: 50,
  filters: [{ field: "status", op: "eq", value: "waiting_approval" }],
  sort:    [{ field: "created_at", direction: "desc" }],
});
```

## CRUD with auto-refresh (generated hooks)

For plain create/read/update/delete, prefer the **generated** TanStack-Query hooks
(`use<Resource>List/Get/Create/Update/Delete`) — a write **auto-invalidates the
matching list**, so the UI updates itself. They need a `QueryClientProvider` (the
scaffold mounts one).

```tsx
import { useRecordList, useRecordCreate, useRecordUpdate } from "lemma-sdk/react";

const list   = useRecordList(client, client.podId, "tickets");          // cached + deduped
const create = useRecordCreate(client, client.podId);
const update = useRecordUpdate(client, client.podId);

create.mutate({ tableName: "tickets", payload: { title: "New ticket", status: "open" } });
// `list` refreshes automatically on success — no refetch wiring.
update.mutate({ tableName: "tickets", recordId, payload: { status: "done" } });
```

Use the hand-written `useRecords` (with `loadMore`) / `useRecordForm` when you need
their richer ergonomics; use the generated hooks when you just want correct CRUD.

## Live updates (subscribe, don't poll)

Two different things keep a list fresh — never a timer:

- **Your own writes** auto-invalidate the matching list via the generated CRUD hooks
  above (TanStack-Query). That covers changes *this* client makes.
- **Other users' / workloads' changes** arrive over the **table WebSocket**. Prefer
  **`useLiveRecords`** — same options as `useRecords`, but it merges insert/update/delete
  deltas **in place** (no flicker, no refetch):

```tsx
import { useLiveRecords } from "lemma-sdk/react";

const { records, isLoading, liveStatus } = useLiveRecords({
  client, podId: client.podId, tableName: "tickets",
  sort: [{ field: "created_at", direction: "desc" }],
});
// render with a stable key={r.id} — rows update in place as the pod changes.
```

Filtered views: the stream carries every change you can *see*, so under the default
`reconcile: "merge"` a new row that doesn't match your `filters` could appear. Keep it
exact with a predicate, or an event-driven refetch:

```tsx
useLiveRecords({ client, tableName: "tickets",
  filters: [{ field: "status", op: "eq", value: "open" }],
  accept: (r) => r.status === "open",   // drop rows that leave the view
  // — or — reconcile: "refetch",        // re-run the query on each change (debounced)
});
```

On the **HTML / no-React path** (or to drive your own state), use the imperative stream
and merge by `record_id`, closing it on teardown:

```js
const rows = new Map();   // id -> row
const handle = client.datastore.watchChanges({
  table: "tickets",
  onChange: (f) => {
    if (f.operation === "delete") rows.delete(f.record_id);
    else rows.set(f.record_id, { ...rows.get(f.record_id), ...f.payload, id: f.record_id });
    render([...rows.values()]);
  },
});
// later: handle.close();
```

Never `setInterval(refetch)` — polling flickers and hammers the API (pod-model
heuristic #4: *never poll a table*).

## Aggregates / cross-table

`useDatastoreQuery(client, podId, sql)` runs a single read-only `SELECT` (RLS still
applies) for counts, group-bys, and joins the record hooks don't cover.

> Exact return fields: `cat /sdk/lemma-typescript/src/react/useRecords.ts` and
> `src/react/generated/records.ts`.
