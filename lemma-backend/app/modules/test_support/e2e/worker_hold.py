"""Hold the session's real worker still while a test drives the work runtime itself.

A session-scoped worker subprocess (``worker``, ``document_worker``, any
``production_worker_process``) runs the full work runtime against the same
database every e2e test in the pytest process uses. A test that claims and runs
items in-process -- ``run_until_idle``, ``make_due``, a hand-built dispatcher --
to observe each transition races that worker for the same rows: the worker takes
the item, runs it without the test's doubles, and the test sees nothing. Alone,
the module passes, because nothing started the worker; packed with any module
that did, it fails.

Such a test module says so with ``pytestmark = pytest.mark.drives_work``; the
root conftest then holds every registered worker for the length of each of its
tests. A held worker is stopped only between transactions -- one frozen inside a
claim would keep queue rows locked and the test's own claim would skip them --
and it always resumes, so the next test that needs it finds it running.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg

from app.modules.test_support.e2e.waiters import eventually


@dataclass(frozen=True, slots=True)
class _Worker:
    pid: int
    database_url: str


_WORKERS: dict[int, _Worker] = {}

_SETTLE_SECONDS = 0.05


def register_session_worker(pid: int, database_url: str) -> None:
    """Record a worker subprocess this pytest process started, so holds can find it."""
    _WORKERS[pid] = _Worker(pid=pid, database_url=database_url)


def unregister_session_worker(pid: int) -> None:
    _WORKERS.pop(pid, None)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _libpq_url(database_url: str) -> str:
    scheme, rest = database_url.split("://", 1)
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _database_names(database_url: str) -> list[str]:
    from app.modules.datastore.config import datastore_settings

    urls = {database_url, datastore_settings.datastore_database_url}
    return sorted({url.rsplit("/", 1)[1].split("?", 1)[0] for url in urls if url})


async def _anyone_in_transaction(database_url: str) -> bool:
    """Whether any client of this process's databases is mid-statement or mid-transaction.

    Asked of the databases, not of the worker's sockets: Docker rewrites the
    client port, so a backend cannot be matched to the worker's connections by
    port. Nothing else in this pytest process is in a transaction while a hold
    is being taken (it is a fixture's setup, after the tables were reset), so a
    busy backend here is the worker's -- or something about to finish, which
    the retry waits out.
    """
    async with await psycopg.AsyncConnection.connect(
        _libpq_url(database_url), autocommit=True
    ) as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = ANY(%s) AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend' AND (state <> 'idle' "
            "OR backend_xid IS NOT NULL OR backend_xmin IS NOT NULL)",
            (_database_names(database_url),),
        )
        row = await cursor.fetchone()
    return bool(row and row[0])


async def _stop_between_transactions(workers: list[_Worker]) -> None:
    """Stop them all at a moment none of them is inside a transaction.

    All at once: the probe sees every client of the databases, so one worker
    left running could keep it busy while another stays stopped mid-way.
    """

    def signal_all(sig: signal.Signals) -> None:
        for worker in workers:
            if _alive(worker.pid):
                os.kill(worker.pid, sig)

    async def stopped() -> bool:
        signal_all(signal.SIGSTOP)
        # A statement a worker wrote just before it stopped may still be in
        # the socket, unread: the backend shows ``idle`` now and opens the
        # transaction a moment later, held for as long as the worker is. Look
        # once that has had time to land.
        await asyncio.sleep(_SETTLE_SECONDS)
        if not await _anyone_in_transaction(workers[0].database_url):
            return True
        signal_all(signal.SIGCONT)
        return False

    await eventually(
        label=f"session workers {[w.pid for w in workers]} idle between transactions",
        probe=stopped,
        done=bool,
        timeout_seconds=30,
        interval_seconds=0.02,
    )


@asynccontextmanager
async def worker_held() -> AsyncIterator[None]:
    """Stop every live registered worker between transactions; resume on exit."""
    for worker in list(_WORKERS.values()):
        if not _alive(worker.pid):
            unregister_session_worker(worker.pid)
    held = list(_WORKERS.values())
    try:
        if held:
            await _stop_between_transactions(held)
        yield
    finally:
        # Also after a failed attempt, which may leave them stopped.
        for worker in held:
            if _alive(worker.pid):
                os.kill(worker.pid, signal.SIGCONT)
