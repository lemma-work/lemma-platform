# Working with data

**Journey:** A person gets their data into a pod — as tables they define or as
documents they drop in — and can then ask questions of it.

A pod holds two kinds of data, and they behave differently on purpose. **Tables**
are structured: the person declares the shape, and the system enforces it.
**Files** are unstructured: the person drops something in, and the system works
out what is inside it. Both end up query-able, and both obey the same rule about
who may see what.

The promise underneath this journey is that data put into a pod stays visible to
the people entitled to it and invisible to everyone else, without the person who
put it there having to think about it on every operation.

---

## Capability: Define tables

### PS-DATA-001 — A person creates a table by declaring its columns
**Status:** covered

- When a person creates a table with a set of columns, the system shall make it
  available for records immediately.
- When a table is created, the system shall record `table.created`.
- The system shall support columns holding text, whole numbers, decimals, true
  or false, dates, timestamps, identifiers, structured JSON, a fixed set of
  choices, a reference to a person, a reference to a file, and an automatically
  increasing number.
- The system shall give every table a primary key, defaulting to one it manages
  when the person does not name their own.
- If a person creates a table whose name collides with an existing table in the
  same pod, then the system shall refuse.
- If a person declares a column name that is not a plain identifier, then the
  system shall refuse and shall say which name was rejected.

**Contracts:** `table.create`, `table.get`, `table.list`, `table.created`

### PS-DATA-002 — A table's shape can change without losing what is in it
**Status:** gap

> **Gap:** removing a column keeps the records, and then makes them unreadable.
> The next read of that table answers 400 `cached statement plan`, because
> dropping a column invalidates asyncpg's per-connection prepared-statement
> cache and nothing catches it. The connection is pooled, so it is not confined
> to whoever ran the change, and it clears when the connection recycles — the
> table appears to break and then fix itself. `DEV-DATA-004`.

- When a person adds a column to a table with records in it, the system shall
  keep every existing record and leave the new column empty on them.
- When a person removes a column, the system shall keep the records and drop
  only that column's values.
- If a person removes the primary key column, then the system shall refuse.
- If a person adds a column whose name is already in use on that table, then the
  system shall refuse.

**Contracts:** `table.column.add`, `table.column.remove`, `table.update`

### PS-DATA-003 — Deleting a table is destructive and says so
**Status:** covered

- When a person with permission deletes a table, the system shall remove the
  table and every record in it.
- The system shall not offer a way to undo it, and shall make that plain at the
  point of deletion rather than after.
- If an agent or function attempts to delete a table, then the system shall
  require an explicit grant or a person's approval for that specific act, even
  when acting on behalf of the person who created the table.

**Contracts:** `table.delete`

> **Note:** pods are deleted softly and tables are deleted permanently. That
> asymmetry is deliberate — a pod is a container a person may want back, a table
> drop is a schema change — but it is the kind of thing that surprises people,
> so the interface has to carry the difference.

---

## Capability: Put records in and get them out

### PS-DATA-010 — A person adds records and the system holds them to the shape
**Status:** covered

- When a person adds a record whose values match the table's columns, the system
  shall store it and return it with its identifier.
- If a person adds a record with a value of the wrong type for its column, then
  the system shall refuse and shall say which column and what it expected.
- If a person adds a record missing a required value, then the system shall
  refuse and shall name the column.
- If a person adds a record with a value outside a column's fixed set of
  choices, then the system shall refuse and shall list the choices.

**Contracts:** `record.create`, `record.get`

### PS-DATA-011 — A person finds the records they want without reading all of them
**Status:** covered

- When a person lists records, the system shall let them filter by column value,
  sort by column, and page through the result.
- The system shall return a stable page boundary, so that paging through an
  unchanging table returns every record exactly once.
- The system shall bound the size of any single page, so that a table with many
  records cannot be pulled in one request by accident.

**Contracts:** `record.list`, `record.get`

### PS-DATA-012 — A person changes and removes records
**Status:** covered

- When a person updates a record, the system shall change only the columns they
  named and leave the rest as they were.
- When a person deletes a record, the system shall remove it and shall leave the
  rest of the table untouched.
- If a person updates or deletes a record that does not exist, then the system
  shall say so rather than reporting success.

**Contracts:** `record.update`, `record.delete`

### PS-DATA-013 — Bulk changes either all happen or none do
**Status:** covered

- When a person creates, updates, or deletes many records in one request, the
  system shall apply all of them or none of them.
- If any record in a bulk request is rejected, then the system shall apply none
  of them and shall say which one failed and why.
- The system shall bound how many records one request may carry, and shall say
  the limit when it is exceeded rather than truncating silently.

**Contracts:** `record.bulk_create`, `record.bulk_update`, `record.bulk_delete`

### PS-DATA-014 — Records respect who is asking
**Status:** covered

- The system shall apply the pod's access rules to every record read and write,
  whoever is asking and through whichever client.
- While an agent or function acts on a person's behalf, the system shall give it
  no more access to records than that person has.
- If a person is entitled to some rows of a table and not others, then the
  system shall return only the rows they are entitled to, rather than refusing
  the whole request.
- If someone outside the pod asks for its records, then the system shall refuse.

**Contracts:** `record.list`, `record.get`, `query.execute`

### PS-DATA-015 — A table's rows belong to whoever wrote them, unless it is shared
**Status:** covered

- The system shall scope a table's rows to the person who created each one, by
  default, so that a table backing a personal app is private without anyone
  configuring it.
- Where a person creates a table as shared, the system shall let every member
  who may read the table read every row in it.
- The system shall apply the same scoping to writes as to reads.

> This is the decision most likely to surprise: a table created with no options
> is *per-owner*, not shared. Two members of the same pod each see only their own
> rows. A support inbox or a team CRM has to be created shared, and the interface
> that creates tables has to make that choice visible — a team discovering it
> from an empty list has already lost time to it.

**Contracts:** `table.create`, `record.list`, `record.create`

### PS-DATA-016 — An administrator can see every row when they ask for it
**Status:** covered

- Where a person may administer a table, the system shall let them ask for every
  member's rows rather than only their own.
- The system shall keep that an explicit request, so that ordinary reads by an
  administrator return the same rows an ordinary member would see.
- If a person who may not administer the table asks for every member's rows,
  then the system shall refuse.

**Contracts:** `record.list`, `query.execute`

---

## Capability: Ask questions across tables

### PS-DATA-020 — A person queries their pod's data directly
**Status:** covered

- When a person runs a query against their pod, the system shall return the rows
  they are entitled to see and no others.
- The system shall enforce that entitlement inside the database rather than by
  filtering afterwards, so that a query cannot be written that steps around it.
- If a query attempts to change data rather than read it, then the system shall
  refuse.
- If a query names a table in another pod, then the system shall refuse.

**Contracts:** `query.execute`

### PS-DATA-021 — When querying is unavailable, the system says so
**Status:** manual

- If the deployment cannot support direct querying, then the system shall report
  that the facility is unavailable, rather than failing each query as though it
  were the person's mistake.
- The system shall keep every query failing closed while it is in that state,
  never falling back to an unfiltered read.

> **Verified by:** `test_a_query_says_the_facility_is_unavailable` in
> `app/modules/datastore/tests/e2e/test_query_unavailable_e2e.py`, not by a
> scenario. Direct querying runs as a dedicated Postgres role
> (`datastore_query_role`), and taking that role away is the only way the state
> exists — which a scenario cannot do: the suite forbids mocking, and dropping
> the role from a shared stack breaks every other scenario using it. Inducing a
> dependency failure is what the module e2e suite is for
> (see [testing.md](../../testing.md)).
>
> That test was written for this promise and immediately failed it: a query
> answered `400 DATASTORE_QUERY_ERROR` with the raw Postgres message, telling
> a person their SQL was wrong when the deployment was the problem. It now
> answers `503 DATASTORE_QUERY_UNAVAILABLE`. The fail-closed half is covered by
> `PS-DATA-020`: a query from outside the pod is refused rather than answered
> unfiltered.

**Contracts:** `query.execute`

---

## Capability: Put documents in

### PS-DATA-030 — A person uploads a file and it lands where they put it
**Status:** covered

- When a person uploads a file to a path, the system shall store it and make it
  listable at that path.
- When a file is added, the system shall record `document.added`.
- When a person uploads to a path whose folders do not exist, the system shall
  create them.
- The system shall bound the size of an upload, and shall say the limit when it
  is exceeded.
- If a person uploads to a path that already holds a file, then the system shall
  either replace it or refuse — and shall do the same thing every time.

**Contracts:** `file.upload`, `file.folder.create`, `file.get`, `document.added`

### PS-DATA-031 — A person browses a pod's files as a tree
**Status:** covered

- When a person asks for the file tree, the system shall return the folders and
  files they are entitled to see, and no others.
- The system shall let a person list one folder's contents without loading the
  whole tree.

**Contracts:** `file.tree`, `file.list`, `file.children.list`

### PS-DATA-032 — A person moves, renames, and deletes files
**Status:** covered

- When a person updates a file's path, the system shall move it and shall keep
  its identity, so references to it continue to resolve.
- When a person deletes a file, the system shall remove it from listings
  immediately and shall clean up its stored bytes and anything derived from it.
- If a person deletes a folder that still has files in it, then the system shall
  say what it is about to remove rather than silently taking the contents.

**Contracts:** `file.update`, `file.delete`, `file.get_by_id`

---

## Capability: Make documents readable and searchable

### PS-DATA-040 — An uploaded document becomes readable text
**Status:** covered

- When a person uploads a document, the system shall convert it to Markdown and
  attach that to the file, so a person or an agent can read it without handling
  the original format.
- The system shall report where a file is in that process — waiting, working,
  ready, or failed — so a person knows whether to keep waiting.
- The system shall do the conversion in the background, so uploading many files
  does not block the person who uploaded them.
- Where a person supplies their own Markdown for a file, the system shall use it
  in place of what it would have extracted.

**Contracts:** `file.upload`, `file.get`, `file.markdown.attach`, `file.markdown.detach`, `file.child.get`

### PS-DATA-041 — A document that fails to convert is not lost
**Status:** covered

- If a document cannot be converted because of what it contains, then the system
  shall mark it failed, keep the original, and stop retrying.
- If a document cannot be converted because the conversion service is
  unavailable, then the system shall retry it later and shall not count the
  attempt against it.
- The system shall keep the original bytes downloadable whatever happens to the
  conversion.
- When a person replaces a failed file's contents, the system shall try again
  from a clean slate.

**Contracts:** `file.upload`, `file.get`, `file.download`

### PS-DATA-042 — One person's bulk upload does not stall everyone else
**Status:** covered

- While a pod is processing many documents, the system shall keep serving
  conversations, surface messages, and workflow runs at their normal pace.
- The system shall share document processing fairly across pods, so that one
  pod's backlog does not starve another's.
- The system shall eventually process every accepted upload, even one it
  declined to start immediately.

**Contracts:** `file.upload`, `file.get`

### PS-DATA-043 — A person searches what is in their documents
**Status:** covered

- When a person searches, the system shall return matches from inside document
  contents, not only from file names.
- The system shall return only matches from files that person is entitled to
  read.
- The system shall say which file each match came from and where in it.

**Contracts:** `file.search`

---

## Capability: Share a file outside the pod

### PS-DATA-050 — A person gets a link to a file that works and then stops
**Status:** covered

- When a person creates a signed link to a file, the system shall let that link
  fetch the file without a session, until it expires.
- When a signed link expires, the system shall refuse it.
- If a file is deleted, then the system shall refuse links to it that have not
  yet expired.
- The system shall keep a signed link scoped to the one file it was made for.

**Contracts:** `file.signed_url`, `file.url`, `file.download`

---

## Capability: Watch data change

### PS-DATA-060 — A person sees records change as they change
**Status:** covered

- While a person is watching a pod's records, the system shall deliver each
  change they are entitled to see, as it happens.
- The system shall deliver no change the watcher is not entitled to see.
- When a watcher reconnects after dropping, the system shall let them resume
  from where they stopped rather than replaying from the beginning or skipping
  the gap.

**Contracts:** `record.create`, `record.update`, `record.delete`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Who is allowed to read which table | [Sharing and permissions](sharing-and-permissions.md) |
| Functions that read and write records | [Automating work](automating-work.md) |
| An agent answering questions from documents | [Agents and conversations](agents-and-conversations.md) |
| Storage backends, ingestion tuning | [Object storage](../../../lemma-backend/docs/operators/object-storage.md) |
