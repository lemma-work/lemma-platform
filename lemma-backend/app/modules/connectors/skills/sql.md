# SQL Database

A generic catalog entry: point an install at a PostgreSQL database and query it.
**Read-only** — every statement is parsed and anything that is not a `SELECT` is
refused, so a compromised prompt cannot write, drop or escalate through it.

Unlike the other two generic entries this one has a **fixed set of three
operations**, because a database describes itself:

| Operation | |
| --- | --- |
| `list_tables` | What is in the database. |
| `describe_table` | One table's columns and types. |
| `execute_query` | A read-only `SELECT`. |

**Install config:** `dialect` (`postgresql`, the only engine supported today),
`host`, `port` (default `5432`), `database` — the database name you would pass to
`psql`, not a schema and not a table.

**Account credentials:** `username` and `password`, both **required**. A
read-only role is enough, and is what you should use: Lemma only ever issues
`SELECT`, so nothing more is needed to make the connector work, and a
write-capable role only widens what a bug could reach.

## Set one up

```bash
lemma connectors auth-configs create sql --kind sql --name warehouse \
  -d '{"dialect": "postgresql", "host": "db.acme.test", "port": 5432, "database": "analytics"}'

lemma connectors accounts create --auth-config warehouse \
  -d '{"username": "reader", "password": "..."}'
```

## Use it

Look before you query — the schema is the server's, not yours to assume:

```bash
lemma connectors run warehouse list_tables -d '{"payload": {}}'
lemma connectors run warehouse describe_table -d '{"payload": {"table": "orders"}}'
lemma connectors run warehouse execute_query \
  -d '{"payload": {"query": "SELECT status, count(*) FROM orders GROUP BY status"}}'
```

## Tips

- **`refresh-operations` does nothing here.** The three operations are fixed;
  there is no per-install discovery to re-run.
- An org can hold several installs — one per database — told apart by `--name`.
  Name them for what they are: two called `replica` are told apart only by the
  host shown beside them.
- **Private, loopback and link-local addresses are refused**, so a database on
  the same machine as Lemma is not reachable this way.
- **`kind`, `connector_id` and `config_source` cannot be changed.** Pointing at
  another database is a new install.
- The config schema is closed: an unknown key is rejected, not stored.
