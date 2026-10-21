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
import pytest


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


_BUSY_BACKENDS = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = ANY(%s) AND pid <> pg_backend_pid() "
    "AND backend_type = 'client backend' AND (state <> 'idle' "
    "OR backend_xid IS NOT NULL OR backend_xmin IS NOT NULL)"
)

#: How long a hold may take before the test fails, and how often a running
#: worker is looked at while the hold waits for it to finish a transaction.
_HOLD_TIMEOUT_SECONDS = 30.0
_RUNNING_POLL_SECONDS = 0.005


async def _anyone_in_transaction(
    connection: psycopg.AsyncConnection, databases: list[str]
) -> bool:
    """Whether any client of this process's databases is mid-statement or mid-transaction.

    Asked of the databases, not of the worker's sockets: Docker rewrites the
    client port, so a backend cannot be matched to the worker's connections by
    port. Nothing else in this pytest process is in a transaction while a hold
    is being taken (it is a fixture's setup, after the tables were reset), so a
    busy backend here is the worker's -- or something about to finish, which
    the retry waits out.
    """
    cursor = await connection.execute(_BUSY_BACKENDS, (databases,))
    row = await cursor.fetchone()
    return bool(row and row[0])


async def _stop_between_transactions(workers: list[_Worker]) -> None:
    """Stop them all at a moment none of them is inside a transaction.

    All at once: the probe sees every client of the databases, so one worker
    left running could keep it busy while another stays stopped mid-way.

    A worker caught inside a transaction is resumed and *left running until it
    is seen between transactions* before the next stop. Retrying the stop on a
    fixed short interval instead gave it a few milliseconds of every cycle to
    finish -- on a loaded machine, less than it needed to be scheduled at all --
    so a hold could spend seconds re-stopping a worker in the same transaction.
    """

    def signal_all(sig: signal.Signals) -> None:
        for worker in workers:
            if _alive(worker.pid):
                os.kill(worker.pid, sig)

    databases = _database_names(workers[0].database_url)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _HOLD_TIMEOUT_SECONDS
    async with await psycopg.AsyncConnection.connect(
        _libpq_url(workers[0].database_url), autocommit=True
    ) as connection:
        while True:
            signal_all(signal.SIGSTOP)
            # A statement a worker wrote just before it stopped may still be in
            # the socket, unread: the backend shows ``idle`` now and opens the
            # transaction a moment later, held for as long as the worker is.
            # Look once that has had time to land.
            await asyncio.sleep(_SETTLE_SECONDS)
            if not await _anyone_in_transaction(connection, databases):
                return
            signal_all(signal.SIGCONT)
            while await _anyone_in_transaction(connection, databases):
                if loop.time() >= deadline:
                    pytest.fail(
                        f"session workers {[w.pid for w in workers]} never went "
                        f"{_HOLD_TIMEOUT_SECONDS:.0f}s without a transaction open"
                    )
                await asyncio.sleep(_RUNNING_POLL_SECONDS)
            if loop.time() >= deadline:
                pytest.fail(
                    f"session workers {[w.pid for w in workers]} could not be "
                    f"stopped between transactions in {_HOLD_TIMEOUT_SECONDS:.0f}s"
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
