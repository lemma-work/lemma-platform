"""`/machines/me`, end to end, against a real Docker machine.

The routes a person, the CLI, the SDK and an app (with its viewer's yes) use
to work on a machine: run a process and read its output, type into it, watch
it over the stream socket, stop it; write, read, move and delete files, and
be refused the browser's profile; copy files to and from the pod; clean up by
scope.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.waiters import eventually
from sandbox_runtime.paths import (
    BROWSER_PROFILE,
    BROWSER_PROFILE_ROOT,
    HOME_ROOT,
    WORKSPACE_ROOT,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]


async def _start(client, command: str, **extra) -> dict:
    response = await client.post(
        "/machines/me/processes", json={"command": command, **extra}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _finished(client, process_id: str) -> dict:
    """Read a process to its end, by pages, the way the Python SDK does."""
    chunks: list[str] = []
    after = 0
    for _ in range(120):
        page = await client.get(
            f"/machines/me/processes/{process_id}/output",
            params={"after": after, "wait_seconds": 2},
        )
        assert page.status_code == status.HTTP_200_OK, page.text
        body = page.json()
        chunks.extend(chunk["data"] for chunk in body["chunks"])
        after = body["next_sequence"]
        if body["state"] not in ("running", "starting", "reserved", "unknown"):
            return {**body, "text": "".join(chunks)}
    raise AssertionError(f"process {process_id} never finished: {''.join(chunks)}")


async def _warm(client) -> None:
    async def probe():
        try:
            started = await client.post(
                "/machines/me/processes", json={"command": "true"}
            )
        except Exception:  # noqa: BLE001 - a cold machine is retried
            return None
        return started

    await eventually(
        label="the machine answering",
        probe=probe,
        done=lambda r: r is not None and r.status_code == status.HTTP_201_CREATED,
        timeout_seconds=300,
        interval_seconds=2.0,
    )


@pytest.fixture
async def machine(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
):
    del local_sandbox_server, configure_workspace_api_url
    await _warm(authenticated_client)
    return {"client": authenticated_client, "server": backend_server}


async def test_the_person_sees_their_own_machine(machine) -> None:
    listed = await machine["client"].get("/machines")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    mine = [m for m in listed.json()["machines"] if m["kind"] == "default"]
    assert len(mine) == 1
    assert mine[0]["ref"] == "me"
    assert mine[0]["access"] == "manage"
    assert mine[0]["capabilities"]["processes"] is True

    one = await machine["client"].get("/machines/me")
    assert one.json()["id"] == mine[0]["id"]


async def test_a_process_runs_to_its_exit_code_and_its_output(machine) -> None:
    client = machine["client"]
    started = await _start(client, "echo out; echo err >&2; exit 3")

    done = await _finished(client, started["id"])

    assert done["exit_code"] == 3
    assert "out" in done["text"] and "err" in done["text"]


async def test_a_process_takes_input_and_is_stopped(machine) -> None:
    client = machine["client"]
    cat = await _start(client, "cat", tty={"cols": 80, "rows": 24})
    typed = await client.post(
        f"/machines/me/processes/{cat['id']}:input", json={"data": "hello machine\n"}
    )
    assert typed.status_code == status.HTTP_204_NO_CONTENT, typed.text

    async def echoed():
        page = await client.get(
            f"/machines/me/processes/{cat['id']}/output", params={"wait_seconds": 1}
        )
        return "".join(chunk["data"] for chunk in page.json()["chunks"])

    await eventually(
        label="the typed line echoed",
        probe=echoed,
        done=lambda text: "hello machine" in text,
        timeout_seconds=30,
    )

    stopped = await client.delete(f"/machines/me/processes/{cat['id']}")
    assert stopped.status_code == status.HTTP_204_NO_CONTENT, stopped.text
    done = await _finished(client, cat["id"])
    assert done["state"] in ("cancelled", "failed", "succeeded")


async def test_the_stream_socket_carries_output_out_and_input_in(
    machine, fixed_test_user
) -> None:
    import websockets

    client = machine["client"]
    shell = await _start(
        client, "bash --noprofile --norc", tty={"cols": 80, "rows": 24}
    )
    url = (
        machine["server"]["host_base_url"].replace("http://", "ws://")
        + f"/machines/me/processes/{shell['id']}/stream"
    )
    seen = ""
    # Typed the way a person types: after the prompt, and after the answer.
    # Input sent before readline sets the terminal up is flushed by it.
    script = [("$ ", "echo $((6*7))\n"), ("42", "exit 5\n")]
    async with websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {fixed_test_user['token']}"},
    ) as socket:
        await socket.send(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        exit_message = None
        async with asyncio.timeout(60):
            async for raw in socket:
                message = json.loads(raw)
                if message["type"] == "output":
                    seen += message["data"]
                    if script and script[0][0] in seen:
                        _, typed = script.pop(0)
                        await socket.send(json.dumps({"type": "input", "data": typed}))
                elif message["type"] == "exit":
                    exit_message = message
                    break
    assert "\r\n42\r\n" in seen, seen
    assert exit_message is not None and exit_message["exit_code"] == 5


async def test_files_are_written_read_moved_and_deleted(machine) -> None:
    client = machine["client"]
    path = f"{WORKSPACE_ROOT}/e2e-{uuid4().hex[:6]}/note.txt"
    await client.post(
        "/machines/me/processes", json={"command": f"mkdir -p {path.rsplit('/', 1)[0]}"}
    )
    await asyncio.sleep(1)

    written = await client.put(
        "/machines/me/files/content", params={"path": path}, content=b"first draft"
    )
    assert written.status_code == status.HTTP_200_OK, written.text

    read = await client.get("/machines/me/files:content", params={"path": path})
    assert read.content == b"first draft"

    moved_to = path.replace("note.txt", "final.txt")
    moved = await client.post(
        "/machines/me/files:move", json={"source": path, "destination": moved_to}
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    assert moved.json()["path"] == moved_to

    gone = await client.delete("/machines/me/files", params={"path": moved_to})
    assert gone.status_code == status.HTTP_204_NO_CONTENT, gone.text
    missing = await client.get("/machines/me/files:stat", params={"path": moved_to})
    assert missing.status_code == status.HTTP_404_NOT_FOUND


async def test_the_browser_profile_cannot_be_read_deleted_or_overwritten(
    machine,
) -> None:
    client = machine["client"]
    profile = f"{BROWSER_PROFILE}/Default/Cookies"

    read = await client.get("/machines/me/files:content", params={"path": profile})
    deleted = await client.delete(
        "/machines/me/files", params={"path": f"{HOME_ROOT}/.lemma", "recursive": True}
    )
    overwritten = await client.put(
        "/machines/me/files/content", params={"path": profile}, content=b"x"
    )
    moved = await client.post(
        "/machines/me/files:move",
        json={"source": BROWSER_PROFILE_ROOT, "destination": f"{WORKSPACE_ROOT}/b"},
    )

    for response in (read, deleted, overwritten, moved):
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


async def test_files_sync_between_the_machine_and_the_pod(
    machine, fixed_test_org
) -> None:
    client = machine["client"]
    pod = await client.post(
        "/pods",
        json={
            "name": f"Sync Pod {uuid4().hex[:6]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    pod_id = pod.json()["id"]
    folder = f"{WORKSPACE_ROOT}/render-{uuid4().hex[:6]}"
    done = await _finished(
        client,
        (
            await _start(
                client,
                f"mkdir -p {folder}/sub && echo one > {folder}/a.txt "
                f"&& echo two > {folder}/sub/b.txt",
            )
        )["id"],
    )
    assert done["exit_code"] == 0

    to_pod = await client.post(
        "/machines/me/files:sync",
        json={
            "direction": "to_pod",
            "machine_path": folder,
            "pod_id": pod_id,
            "pod_path": "/me/renders",
        },
    )
    assert to_pod.status_code == status.HTTP_200_OK, to_pod.text
    assert len(to_pod.json()["files"]) == 2

    back = f"{WORKSPACE_ROOT}/back-{uuid4().hex[:6]}.txt"
    from_pod = await client.post(
        "/machines/me/files:sync",
        json={
            "direction": "from_pod",
            "machine_path": back,
            "pod_id": pod_id,
            "pod_path": to_pod.json()["files"][0],
        },
    )
    assert from_pod.status_code == status.HTTP_200_OK, from_pod.text
    read = await client.get("/machines/me/files:content", params={"path": back})
    assert read.content.strip() in (b"one", b"two")


async def test_cleaning_up_by_scope_and_the_home_needs_a_yes(machine) -> None:
    client = machine["client"]
    await _finished(
        client,
        (
            await _start(
                client,
                f"mkdir -p {HOME_ROOT}/.cache/e2e && head -c 2000000 /dev/zero > "
                f"{HOME_ROOT}/.cache/e2e/blob",
            )
        )["id"],
    )

    cleaned = await client.post(
        "/machines/me:reset", json={"scopes": ["caches", "processes"]}
    )
    assert cleaned.status_code == status.HTTP_200_OK, cleaned.text
    scopes = {result["scope"]: result for result in cleaned.json()["results"]}
    assert set(scopes) == {"caches", "processes"}
    gone = await client.get(
        "/machines/me/files:stat", params={"path": f"{HOME_ROOT}/.cache/e2e/blob"}
    )
    assert gone.status_code == status.HTTP_404_NOT_FOUND

    unconfirmed = await client.post("/machines/me:reset", json={"scopes": ["home"]})
    assert unconfirmed.status_code == status.HTTP_409_CONFLICT, unconfirmed.text
