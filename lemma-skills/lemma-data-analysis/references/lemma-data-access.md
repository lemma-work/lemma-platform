# Lemma Data Access

Use normal Lemma identity and authorization paths. Analysis is read-only by
default; do not create, update, delete, import, or alter records or tables to make
an analysis easier.

## Orient and inspect schemas

```bash
lemma pods list
lemma pods describe
lemma tables list
lemma tables get orders
lemma records list orders --limit 20
lemma records get orders <record-id>
```

Read `lemma tables get <table>` before querying. Capture:

- the table name, primary key, business columns, types, enums, and foreign keys;
- `enable_rls` and whether the intended population is personal or shared;
- the analytical grain and whether multiple rows can represent one event/entity;
- the meaning of `created_at` and `updated_at` versus business event timestamps.

Lemma adds `id`, `created_at`, and `updated_at` to every table and `user_id` to
RLS tables. Treat them as system-managed fields. A sample proves shape, not
coverage or completeness.

## Choose the access path

### Aggregate or join with read-only SQL

```bash
lemma query run "SELECT status, COUNT(*) AS total FROM orders GROUP BY status"
lemma --output json query run "SELECT status, COUNT(*) AS total FROM orders GROUP BY status"
```

`query run` accepts one read-only `SELECT` and supports joins, aggregates, and
subqueries across pod tables. Start with counts and small grouped results. Apply
time and population filters explicitly. Use conservative SQL and test unfamiliar
functions in a small query rather than assuming a dialect feature exists.

### Export row-level data

```bash
lemma records export orders ./inputs/orders.csv --limit 25000
lemma records export orders ./inputs/orders.jsonl --limit 25000
```

CSV is the default; `.jsonl` and `.json` are supported. The default export limit
is 10,000 rows. Count the intended population first, pass an explicit limit, and
verify the exported row count so a capped extract is never mistaken for the
population. Keep the first extract immutable; derive cleaned data separately.

### Download a tabular pod file

```bash
lemma files stat /data/orders.xlsx
lemma files download /data/orders.xlsx ./inputs/orders.xlsx
```

CSV, JSON, XLSX, and images are stored but not search-indexed. Download the exact
bytes, preserve the original, and record the remote path plus file metadata.
Use `lemma files cat` or `lemma files search` for indexed documents, not for
discovering rows inside a spreadsheet.

## Respect RLS and grants

Normal table reads, exports, and queries run as the current user. On an RLS table
they return only rows visible to that identity; shared tables expose the common
row set. An empty list or `404` can mean another user owns the row, not that the
data does not exist. Label every result as personal/RLS-scoped or shared.

Do not switch to an admin or cross-user read merely to fill an evidence gap. If
the requested population exceeds the current scope, report what is visible and
request the specific authorized path. If a workload receives
`MISSING_WORKLOAD_RESOURCE_GRANT`, preserve the exact error and ask a builder to
grant the named resource; never bypass the grant.

## Record provenance

For every source, retain:

- pod and table name or remote file path;
- exact SQL or extraction command;
- execution timestamp and timezone;
- identity/RLS scope and filters;
- rows expected, returned, excluded, and joined;
- source freshness and known update lag;
- immutable local input filename and, for high-stakes work, a checksum.

Keep credentials and injected `LEMMA_*` values out of scripts, reports, command
logs, and uploaded artifacts.
