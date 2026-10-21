"""Per-user rows over the live sockets when the database role obeys row security.

Per-user tables are ``FORCE ROW LEVEL SECURITY``: the policy binds the table's
owner too, and only a superuser or a ``BYPASSRLS`` role is let past it. The e2e
database connects as a superuser, so every other live test reads current rows
straight through. A deployment's own role (a managed Postgres's app user, an
operator's cluster) is neither, and there a read that names no row principal
matches no per-user row: the hub fetched nothing, took every change as "gone
since it was logged", and sent nothing -- not even to the row's writer -- while
shared tables, which have no policy, kept working.

These tests read current rows through a role that obeys row security, the way
such a deployment does, and hold the socket to the rule the REST API keeps: a
per-user row reaches the person who wrote it, and nobody else.
"""

from __future__ import annotations

import asyncio
import functools
import json
import secrets
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from websockets.asyncio.client import ClientConnection, connect

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.changes import reader
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.runtime import backend_server as _backend_server

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server

_COLUMNS = [{"name": "body", "type": "TEXT", "required": False}]


async def _tables(pod_api: DatastoreApi) -> None:
    for name, per_user in (("notes", True), ("board", False)):
        await pod_api.create_table(
            {"name": name, "enable_rls": per_user, "columns": _COLUMNS}
        )


@pytest_asyncio.fixture
async def rls_bound_engine(pod_api: DatastoreApi) -> AsyncIterator[AsyncEngine]:
    """An engine on the datastore database as a role row security binds.

    ``NOSUPERUSER NOBYPASSRLS``, with read access to the pod's tables -- what
    the API's own role is in a deployment that is not run as a superuser.
    Created after the tables, so ``ON ALL TABLES`` covers them.
    """
    await _tables(pod_api)
    schema = schema_name_for_pod(UUID(pod_api.pod_id))
    role = f"lemma_live_rls_{secrets.token_hex(6)}"
    password = secrets.token_hex(16)
    admin = get_datastore_engine()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        await conn.execute(
            text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        )
    engine = create_async_engine(
        admin.url.set(username=role, password=password), pool_size=2
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP OWNED BY "{role}"'))
            await conn.execute(text(f'DROP ROLE "{role}"'))


@pytest.fixture
def hub_reads_as_rls_bound_role(monkeypatch, rls_bound_engine: AsyncEngine) -> None:
    """The live hub's current-row fetch goes through the RLS-bound role."""
    monkeypatch.setattr(
        reader,
        "fetch_rows",
        functools.partial(reader.fetch_rows, engine=rls_bound_engine),
    )


async def _table_context(pod_id: str, name: str) -> TableContext:
    async with async_session_maker() as session:
        repository = DatastoreTableRepository(SqlAlchemyUnitOfWork(session))
        entity = await repository.get_by_datastore_and_name(UUID(pod_id), name)
    assert entity is not None
    return TableContext.from_table_entity(entity, schema_name_for_pod(UUID(pod_id)))


async def test_current_rows_are_read_through_a_role_row_security_binds(
    pod_api: DatastoreApi, rls_bound_engine: AsyncEngine
) -> None:
    mine = await pod_api.create_record("notes", {"body": "per-user row"})
    ours = await pod_api.create_record("board", {"body": "shared row"})

    for name, record in (("notes", mine), ("board", ours)):
        table = await _table_context(pod_api.pod_id, name)
        rows = await reader.fetch_rows(
            UUID(pod_api.pod_id), table, [record["id"]], engine=rls_bound_engine
        )
        assert rows[record["id"]]["body"] == record["body"], name


# -- the sockets --------------------------------------------------------------


class _Socket:
    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.frames: list[dict] = []

    async def send(self, **frame: object) -> None:
        await self.ws.send(json.dumps(frame))

    async def recv(self, timeout: float) -> dict:
        frame = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
        self.frames.append(frame)
        return frame

    async def until(self, match, timeout: float = 20) -> dict:
        async def _next() -> dict:
            while True:
                frame = await self.recv(timeout)
                if match(frame):
                    return frame

        return await asyncio.wait_for(_next(), timeout)

    async def quiet(self, seconds: float = 2.0) -> None:
        try:
            async with asyncio.timeout(seconds):
                while True:
                    await self.recv(seconds)
        except TimeoutError:
            return

    def record_ids(self) -> set[str]:
        """Every record id any frame on this socket carried."""
        ids: set[str] = set()
        for frame in self.frames:
            if frame.get("type") == "changes":
                ids.update(change["record_id"] for change in frame["changes"])
            elif frame.get("record_id"):
                ids.add(str(frame["record_id"]))
        return ids


def _url(server: dict[str, str], path: str) -> str:
    return server["host_base_url"].replace("http://", "ws://") + path


@pytest_asyncio.fixture
async def sockets() -> AsyncIterator[list[_Socket]]:
    opened: list[_Socket] = []
    yield opened
    for socket in opened:
        await socket.ws.close()


async def _live(server, pod_id: str, token: str, opened: list[_Socket]) -> _Socket:
    ws = await connect(
        _url(server, f"/pods/{pod_id}/live"),
        additional_headers={"Authorization": f"Bearer {token}"},
    )
    socket = _Socket(ws)
    opened.append(socket)
    assert (await socket.recv(15))["type"] == "ready"
    return socket


async def _legacy(server, pod_id: str, token: str, opened: list[_Socket]) -> _Socket:
    """The previous socket, as a browser opens it: every table, token in the
    query string."""
    ws = await connect(
        _url(server, f"/pods/{pod_id}/datastore/changes?access_token={token}")
    )
    socket = _Socket(ws)
    opened.append(socket)
    assert (await socket.recv(15))["type"] == "ready"
    return socket


def _changes_for(sub: str):
    return lambda frame: frame.get("type") == "changes" and frame.get("id") == sub


async def _subscribe(socket: _Socket, sub: str, table: str | None = None) -> None:
    frame: dict[str, object] = {"op": "subscribe", "id": sub, "topic": "records"}
    if table is not None:
        frame["table"] = table
    await socket.send(**frame)
    await socket.until(lambda f: f.get("type") == "subscribed" and f.get("id") == sub)


@pytest.mark.usefixtures("hub_reads_as_rls_bound_role")
async def test_live_socket_sends_a_per_user_row_to_its_writer_only(
    pod_api: DatastoreApi,
    member_users,
    async_client,
    fixed_test_user,
    backend_server,
    sockets,
) -> None:
    editor = member_users["editor"]
    editor_api = DatastoreApi(async_client, pod_api.pod_id, user=editor)
    writer = await _live(
        backend_server, pod_api.pod_id, fixed_test_user["token"], sockets
    )
    other = await _live(backend_server, pod_api.pod_id, editor["token"], sockets)
    for socket in (writer, other):
        await _subscribe(socket, "notes", "notes")
        await _subscribe(socket, "all")

    written = await pod_api.create_record("notes", {"body": "writer's note"})

    # Both of the writer's subscriptions -- the table, and every table.
    for sub in ("notes", "all"):
        if not any(_changes_for(sub)(f) for f in writer.frames):
            await writer.until(_changes_for(sub))
        [frame] = [f for f in writer.frames if _changes_for(sub)(f)]
        [change] = frame["changes"]
        assert change["record_id"] == written["id"], sub
        assert change["row"]["body"] == "writer's note", sub
    # Somebody row security keeps from the row hears nothing of it.
    await other.quiet()
    assert written["id"] not in other.record_ids()

    # And the other way round: the editor's own row reaches the editor only.
    theirs = await editor_api.create_record("notes", {"body": "editor's note"})
    await other.until(_changes_for("notes"))
    await writer.quiet()
    assert other.record_ids() == {theirs["id"]}
    assert writer.record_ids() == {written["id"]}


@pytest.mark.usefixtures("hub_reads_as_rls_bound_role")
async def test_previous_socket_sends_a_per_user_row_to_its_writer_only(
    pod_api: DatastoreApi,
    member_users,
    fixed_test_user,
    backend_server,
    sockets,
) -> None:
    writer = await _legacy(
        backend_server, pod_api.pod_id, fixed_test_user["token"], sockets
    )
    other = await _legacy(
        backend_server, pod_api.pod_id, member_users["editor"]["token"], sockets
    )

    written = await pod_api.create_record("notes", {"body": "writer's note"})

    frame = await writer.until(lambda f: f.get("record_id") == written["id"])
    assert frame["operation"] == "INSERT"
    assert frame["payload"]["body"] == "writer's note"
    await other.quiet()
    assert written["id"] not in other.record_ids()
