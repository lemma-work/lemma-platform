"""An Agent Host's bulk bytes cross HTTP, not the link: real routes, Redis, DB.

``domain/agent_host_transfer``. The API reads no websocket message past 1 MiB,
so an ``op`` names a transfer and the host moves the bytes over
``/agent-host/transfers/{id}``; a run event names a blob the host put under
``/agent-host/runs/{run_id}/blobs/{sha256}`` first. The host here is the test,
speaking the link as ``HostLink`` and HTTP as the host's ``TransferClient``
does; the caller is the production op client. The real binary doing the same
is ``test_host_execution_binary_e2e.py``'s 2 MiB round trip.

What each route must refuse is the point of most of these: a transfer is used
once, by the host it was made for, with the bytes it was described by, before
it expires.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.contracts.host_execution import (
    AgentHostOpClient,
    AgentHostOpError,
)
from app.modules.agent.domain.agent_host_ops import TRANSFER_INCOMPLETE
from app.modules.agent.domain.agent_host_transfer import (
    BLOB_MAX_BYTES,
    DIGEST_HEADER,
    LINK_INLINE_DATA_BYTES,
)
from app.modules.agent.infrastructure.agent_host.transfers import (
    AgentHostTransferStore,
)
from app.modules.agent.services.agent_host_link_ops import OLD_HOST_OUTPUT_LIMIT_BYTES
from app.modules.agent.tests.e2e.agent_host_helpers import (
    app_of,
    connected_host,
    conversation_with_a_leased_run,
    pair,
    paired_machine,
)

pytestmark = pytest.mark.e2e

#: A current host: it moves file data over HTTP itself.
_ON = {"enabled": True, "platform": "macos", "available": True, "transfers": True}
#: A host released before transfers: its link session moves the data for it.
_OLD = {"enabled": True, "platform": "macos", "available": True}


def _deadline(seconds: float = 20) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _as(machine: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {machine['host_secret']}"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _next_op(link) -> dict:
    while True:
        frame = await link.next_push(timeout=10)
        if frame["type"] == "op":
            return frame


@pytest.mark.asyncio
async def test_a_write_and_a_read_move_their_data_over_http_not_the_link(
    authenticated_client, async_client
):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    link = await connected_host(app_of(async_client), machine, host_execution=_ON)
    host_id, workspace = UUID(machine["host_id"]), uuid4()
    # Past the message ceiling once base64'd, as a whole chunk is.
    chunk = bytes(range(256)) * 4096
    try:
        # --- a write: the op names the staged chunk; the host fetches it ----
        writing = asyncio.ensure_future(
            AgentHostOpClient().send(
                host_id=host_id,
                workspace=workspace,
                method="file.write",
                params={"path": "/w/f", "upload_id": "u", "offset": 0, "final": True},
                data=chunk,
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        params = frame["body"]["params"]
        assert "data" not in params
        transfer = params["transfer"]
        assert transfer["size"] == len(chunk)
        assert transfer["sha256"] == _sha(chunk)

        fetched = await async_client.get(
            f"/agent-host/transfers/{transfer['id']}", headers=_as(machine)
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.content == chunk
        assert fetched.headers[DIGEST_HEADER] == _sha(chunk)
        # Once.
        again = await async_client.get(
            f"/agent-host/transfers/{transfer['id']}", headers=_as(machine)
        )
        assert again.status_code == 409

        link.reply("op_ok", frame["id"], {"result": {"size_bytes": len(chunk)}})
        assert await asyncio.wait_for(writing, timeout=10) == {"size_bytes": len(chunk)}

        # --- a read: the op names a slot; the host delivers into it ---------
        reading = asyncio.ensure_future(
            AgentHostOpClient().receive(
                host_id=host_id,
                workspace=workspace,
                method="file.read",
                params={"path": "/w/f", "offset": 0, "length": len(chunk)},
                max_bytes=len(chunk),
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        slot = frame["body"]["params"]["transfer"]
        assert slot["max_bytes"] == len(chunk)
        delivered = await async_client.put(
            f"/agent-host/transfers/{slot['id']}",
            content=chunk,
            headers={**_as(machine), DIGEST_HEADER: _sha(chunk)},
        )
        assert delivered.status_code == 204, delivered.text
        link.reply(
            "op_ok",
            frame["id"],
            {"result": {"eof": True, "size": len(chunk), "sha256": _sha(chunk)}},
        )
        result, data = await asyncio.wait_for(reading, timeout=10)
        assert data == chunk
        assert result["eof"] is True
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_a_host_released_before_transfers_still_reads_and_writes(
    authenticated_client, async_client
):
    """Its link session does the host's half: the chunk goes inline, as the
    old host reads it, and a read asks for no more than base64 keeps inside
    the message ceiling, delivered into the slot for the caller."""
    machine = await pair(authenticated_client, async_client, display_name="old")
    link = await connected_host(app_of(async_client), machine, host_execution=_OLD)
    host_id, workspace = UUID(machine["host_id"]), uuid4()
    try:
        writing = asyncio.ensure_future(
            AgentHostOpClient().send(
                host_id=host_id,
                workspace=workspace,
                method="file.write",
                params={"path": "/w/f", "upload_id": "u", "offset": 0, "final": True},
                data=b"for the old host",
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        params = frame["body"]["params"]
        assert "transfer" not in params
        assert base64.b64decode(params["data"]) == b"for the old host"
        link.reply("op_ok", frame["id"], {"result": {}})
        assert await asyncio.wait_for(writing, timeout=10) == {}

        reading = asyncio.ensure_future(
            AgentHostOpClient().receive(
                host_id=host_id,
                workspace=workspace,
                method="file.read",
                params={"path": "/w/f", "offset": 0, "length": 1024 * 1024},
                max_bytes=1024 * 1024,
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        params = frame["body"]["params"]
        assert "transfer" not in params
        assert params["length"] == LINK_INLINE_DATA_BYTES
        link.reply(
            "op_ok",
            frame["id"],
            {
                "result": {
                    "data": base64.b64encode(b"inline answer").decode(),
                    "eof": True,
                }
            },
        )
        result, data = await asyncio.wait_for(reading, timeout=10)
        assert data == b"inline answer"
        assert result["eof"] is True and "data" not in result
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_an_old_hosts_process_keeps_no_more_output_than_one_message_holds(
    authenticated_client, async_client
):
    """A host released before transfers ignores ``process.read``'s
    ``max_bytes`` and answers with what its process kept since the last read.
    Started with no more than ``OLD_HOST_OUTPUT_LIMIT_BYTES`` kept, no read of
    it can pass the message ceiling; a current host is started as asked."""
    for name, host_execution, kept in (
        ("old", _OLD, OLD_HOST_OUTPUT_LIMIT_BYTES),
        ("current", _ON, 1024 * 1024),
    ):
        machine = await pair(authenticated_client, async_client, display_name=name)
        link = await connected_host(
            app_of(async_client), machine, host_execution=host_execution
        )
        try:
            starting = asyncio.ensure_future(
                AgentHostOpClient().request(
                    host_id=UUID(machine["host_id"]),
                    workspace=uuid4(),
                    method="process.start",
                    params={
                        "operation_id": "build",
                        "shell_command": "make",
                        "output_limit_bytes": 1024 * 1024,
                    },
                    deadline_at=_deadline(),
                )
            )
            frame = await _next_op(link)
            assert frame["body"]["params"]["output_limit_bytes"] == kept, name
            link.reply("op_ok", frame["id"], {"result": {"process_id": "build"}})
            assert await asyncio.wait_for(starting, timeout=10) == {
                "process_id": "build"
            }
        finally:
            await link.aclose()


@pytest.mark.asyncio
async def test_a_read_whose_data_never_arrived_is_not_a_read(
    authenticated_client, async_client
):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    link = await connected_host(app_of(async_client), machine, host_execution=_ON)
    try:
        reading = asyncio.ensure_future(
            AgentHostOpClient().receive(
                host_id=UUID(machine["host_id"]),
                workspace=uuid4(),
                method="file.read",
                params={"path": "/w/f", "offset": 0, "length": 5},
                max_bytes=5,
                deadline_at=_deadline(),
            )
        )
        frame = await _next_op(link)
        slot = frame["body"]["params"]["transfer"]
        await async_client.put(
            f"/agent-host/transfers/{slot['id']}",
            content=b"other",
            headers={**_as(machine), DIGEST_HEADER: _sha(b"other")},
        )
        # The answer describes bytes that are not the ones delivered.
        link.reply(
            "op_ok",
            frame["id"],
            {"result": {"eof": True, "size": 5, "sha256": _sha(b"hello")}},
        )
        with pytest.raises(AgentHostOpError) as raised:
            await asyncio.wait_for(reading, timeout=10)
        assert raised.value.kind == TRANSFER_INCOMPLETE
        assert raised.value.retryable
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_a_transfer_is_only_for_its_own_host_with_its_own_bytes(
    authenticated_client, async_client
):
    mine = await pair(authenticated_client, async_client, display_name="mine")
    other = await pair(authenticated_client, async_client, display_name="other")
    store = AgentHostTransferStore()
    host_id = UUID(mine["host_id"])

    staged = await store.stage_for_host(
        host_id=host_id,
        workspace=uuid4(),
        data=b"secret file",
        ttl_seconds=60,
    )
    path = f"/agent-host/transfers/{staged.id}"
    # No credential, a made-up one, and another paired host: none of them.
    assert (await async_client.get(path)).status_code == 401
    made_up = {"Authorization": "Bearer not-a-host"}
    assert (await async_client.get(path, headers=made_up)).status_code == 401
    assert (await async_client.get(path, headers=_as(other))).status_code == 404
    # Nor a delivery into a transfer that is a fetch.
    wrong_way = await async_client.put(
        path, content=b"x", headers={**_as(mine), DIGEST_HEADER: _sha(b"x")}
    )
    assert wrong_way.status_code == 404
    # Its own host still gets it, untouched by the refusals.
    fetched = await async_client.get(path, headers=_as(mine))
    assert fetched.status_code == 200 and fetched.content == b"secret file"

    slot = await store.open_for_host(
        host_id=host_id, workspace=uuid4(), max_bytes=8, ttl_seconds=60
    )
    path = f"/agent-host/transfers/{slot.id}"
    lying = await async_client.put(
        path, content=b"abc", headers={**_as(mine), DIGEST_HEADER: _sha(b"abx")}
    )
    assert lying.status_code == 422
    unlabeled = await async_client.put(path, content=b"abc", headers=_as(mine))
    assert unlabeled.status_code == 422
    too_big = await async_client.put(
        path,
        content=b"123456789",
        headers={**_as(mine), DIGEST_HEADER: _sha(b"123456789")},
    )
    assert too_big.status_code == 413
    by_other = await async_client.put(
        path, content=b"abc", headers={**_as(other), DIGEST_HEADER: _sha(b"abc")}
    )
    assert by_other.status_code == 404
    first = await async_client.put(
        path, content=b"abc", headers={**_as(mine), DIGEST_HEADER: _sha(b"abc")}
    )
    assert first.status_code == 204
    second = await async_client.put(
        path, content=b"xyz", headers={**_as(mine), DIGEST_HEADER: _sha(b"xyz")}
    )
    assert second.status_code == 409
    assert await store.collect(slot.id, size=3, sha256=_sha(b"abc")) == b"abc"


@pytest.mark.asyncio
async def test_a_transfer_past_its_lifetime_is_gone(authenticated_client, async_client):
    machine = await pair(authenticated_client, async_client, display_name="mac")
    staged = await AgentHostTransferStore().stage_for_host(
        host_id=UUID(machine["host_id"]),
        workspace=uuid4(),
        data=b"short-lived",
        ttl_seconds=1,
    )
    await asyncio.sleep(1.5)
    expired = await async_client.get(
        f"/agent-host/transfers/{staged.id}", headers=_as(machine)
    )
    assert expired.status_code == 404


@pytest.mark.asyncio
async def test_a_blob_is_taken_only_for_a_run_its_host_holds(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Blobs")
    machine = await paired_machine(scenario)
    other = await paired_machine(scenario, display_name="other machine")
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    await db_session.commit()
    client = scenario.async_client
    image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
    path = f"/agent-host/runs/{run_id}/blobs/{_sha(image)}"
    headers = {**_as(machine), DIGEST_HEADER: _sha(image)}

    stored = await client.put(path, content=image, headers=headers)
    assert stored.status_code == 204, stored.text
    # Again, as a replay sends it: harmless.
    assert (await client.put(path, content=image, headers=headers)).status_code == 204
    blobs = AgentHostTransferStore()
    assert await blobs.get_blob(run_id=run_id, sha256=_sha(image)) == image

    # Another host's run, a run that is nobody's, the wrong bytes, too many.
    assert (
        await client.put(
            path, content=image, headers={**_as(other), DIGEST_HEADER: _sha(image)}
        )
    ).status_code == 404
    assert (
        await client.put(
            f"/agent-host/runs/{uuid4()}/blobs/{_sha(image)}",
            content=image,
            headers=headers,
        )
    ).status_code == 404
    assert (
        await client.put(
            f"/agent-host/runs/{run_id}/blobs/{_sha(b'else')}",
            content=image,
            headers=headers,
        )
    ).status_code == 422
    huge = b"\x00" * (BLOB_MAX_BYTES + 1)
    assert (
        await client.put(
            f"/agent-host/runs/{run_id}/blobs/{_sha(huge)}",
            content=huge,
            headers={**_as(machine), DIGEST_HEADER: _sha(huge)},
        )
    ).status_code == 413
    assert (await client.put(path, content=image)).status_code == 401
