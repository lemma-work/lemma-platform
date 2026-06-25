"""Postgres connection monitor for load tests.

Polls pg_stat_activity every N seconds and logs the active connection count
per database, plus a breakdown by state (active, idle, idle in transaction).

Run as a sidecar during k6 load tests:

    python load_tests/db_monitor.py [--interval 5] [--database-url postgresql://postgres:postgres@localhost:5432/lemma]

The output is CSV-friendly so it can be piped to a file and plotted:

    timestamp,total,active,idle,idle_in_transaction,lemma,lemma_datastore,supertokens
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

try:
    import asyncpg
except ImportError:
    print("asyncpg not installed — run: uv add asyncpg", file=sys.stderr)
    sys.exit(1)


QUERY = """
SELECT
    COALESCE(datname, '(null)') AS db,
    state,
    COUNT(*)::int AS cnt
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
GROUP BY datname, state
ORDER BY datname, state
"""


async def monitor(database_url: str, interval: float, max_samples: int | None) -> None:
    conn = await asyncpg.connect(database_url)

    sample = 0
    try:
        while True:
            sample += 1
            rows = await conn.fetch(QUERY)

            total = sum(r["cnt"] for r in rows)
            active = sum(r["cnt"] for r in rows if r["state"] == "active")
            idle = sum(r["cnt"] for r in rows if r["state"] == "idle")
            idle_in_txn = sum(
                r["cnt"] for r in rows if r["state"] == "idle in transaction"
            )

            by_db: dict[str, int] = {}
            for r in rows:
                by_db[r["db"]] = by_db.get(r["db"], 0) + r["cnt"]

            ts = datetime.now().isoformat(timespec="seconds")
            db_cols = [
                by_db.get("lemma", 0),
                by_db.get("lemma_datastore", 0),
                by_db.get("supertokens", 0),
            ]
            print(
                f"{ts},{total},{active},{idle},{idle_in_txn},"
                + ",".join(str(c) for c in db_cols),
                flush=True,
            )

            if max_samples is not None and sample >= max_samples:
                break
            await asyncio.sleep(interval)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Postgres connections")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--database-url",
        default="postgresql://postgres:postgres@localhost:5432/lemma",
        help="Postgres connection URL (must connect to a DB on the same cluster)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Number of samples to collect (default: run forever)",
    )
    args = parser.parse_args()

    print(
        "timestamp,total,active,idle,idle_in_transaction,lemma,lemma_datastore,supertokens",
        flush=True,
    )
    asyncio.run(monitor(args.database_url, args.interval, args.samples))


if __name__ == "__main__":
    main()
