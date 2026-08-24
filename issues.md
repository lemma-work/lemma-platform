# Issues

Bugs, unexpected behaviour, and places where the implementation does not deliver
what [the product specification](docs/product/README.md) says it should.

Tracked in git on purpose. Each entry is something that was found once,
verified against the code, and understood — writing it down is what stops it
being rediscovered from scratch later. A finding here is not a plan or a
roadmap: it is a statement about how the system behaves today, with a citation.

**Every entry is verified by reading the code or by running against it, never
inferred from a route name or a test name.** Each one cites `file:line`, and
says how it was found.

When a finding is fixed, delete its entry in the pull request that fixes it. A
register of already-fixed bugs is worse than no register — it teaches people to
stop trusting the file.

Ids are stable and append-only, so a `DEV-` reference in a scenario, a commit
message, or a code comment resolves to something.

## Format

```
### DEV-<AREA>-<NNN> — one-line summary
**Violates:** PS-<AREA>-<NNN>
**Severity:** high | medium | low | question
**Where:** path:line
**Required:** what the spec says must happen.
**Actual:** what happens instead.
**Why it matters:** the user-visible consequence.
**Fix:** the shape of the change.
```

Severity `question` means the divergence may be deliberate and the spec may be
the thing that is wrong — resolve it with a product decision before writing code.

## Open

### DEV-DATA-004 — Removing a column makes the table unreadable until the connection recycles
**Violates:** `PS-DATA-002`
**Severity:** high
**Where:** [`db/session.py:33-52`](lemma-backend/app/core/infrastructure/db/session.py#L33)
— `_build_connect_args` sets `server_settings` and nothing else. Neither engine
passes `statement_cache_size`, and no handler for asyncpg's
`InvalidCachedStatementError` exists anywhere in the codebase.

**Required:** A table's shape can change without losing what is in it. Somebody
removes a column they no longer want and reads the table.

**Actual:** The read answers **400**, body `cached statement plan`. Exactly
reproducible, and the order matters:

```
create table (subject, body); add a record
add column `priority`  -> read OK, priority is null on the existing row
remove column `body`   -> read 400  <- here
```

**Why:** asyncpg keeps a per-connection prepared-statement cache. Dropping a
column invalidates the cached plan for the statement that reads that table, and
the next borrower of that pooled connection gets the driver error rather than
rows. Nothing catches it, so it leaves as a 400.

**Why it matters:** the connection is *pooled*, so the damage is not confined to
whoever ran the DDL. Any request that lands on that connection sees the table as
broken, and it stays that way until the connection is recycled (`pool_recycle`,
300s on the datastore engine). A person who removes a column sees their table
stop working, then start working again, with nothing they did in between — which
is close to unreportable.

**Not established:** why the `add` case survives and only `remove` fails. The
run above shows the read after `add column` succeeding, but both are DDL against
the same relation, so the difference may be which pooled connection each request
happened to land on rather than anything about the operation. Worth settling
before choosing the fix, because "invalidate on drop" would be the wrong shape
if add is equally affected and merely luckier.

**Fix:** the two standard shapes are `statement_cache_size=0` in `connect_args`
(simple, costs the cache everywhere) or catching `InvalidCachedStatementError`
and retrying once on a fresh statement (keeps the cache, and is what the error
is for). Choosing between them wants the answer to the paragraph above.

**Found by:**
[`test_adding_and_removing_columns_keeps_the_records`](tests/scenarios/journeys/working_with_data/test_tables_and_records.py),
against dev. It passes against a locally booted stack, where the pool is small
and short-lived enough that the poisoned connection is rarely the next one
borrowed — which is why a deployment run found it and 400+ local scenarios did
not.
