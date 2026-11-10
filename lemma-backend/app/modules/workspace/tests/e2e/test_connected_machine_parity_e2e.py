"""The parity suite: the same machine journeys on every kind of connected machine.

Each lane joins a machine the way a person does and runs ``machine_journeys``
on it; the two Docker lanes also run PR 1's ``/machines`` journeys
(``test_machines_api_e2e``) unchanged, with ``me`` meaning the connected
machine:

* **host, in process** -- the agent and runtime in this interpreter, under a
  temporary ``--root``. Fast; no Docker.
* **host, in a throwaway container** -- the real ``lemma machine connect``
  inside a bare ``python:3.14-slim`` container standing in for someone's VM,
  rooted at ``/home/user`` so PR 1's journeys find the paths they name:
  it pairs, builds its uv environment from the server's lockfile, installs the
  server's runtime over HTTP and serves it as that container's ordinary user.
  It builds its environment from scratch, so it is ``slow``: the scheduled
  protected lane runs it, not the pull-request lane.
* **image** -- the real ``lemma machine connect --image`` on this machine,
  running the ``lemma-workspace`` image under Docker; this lane also proves the
  browser's relay answers over the link.

The two Docker lanes are ``workspace``-marked like the rest of the real-image
suite. Each ends by disconnecting the way the person would and checking the
machine is gone.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.infrastructure import runtime_bundle as bundle_module
from app.modules.workspace.providers.base import ProviderInstance
from app.modules.workspace.providers.desktop_tunnel import sandbox_transport
from app.modules.workspace.tests.e2e import machine_journeys
from app.modules.workspace.tests.e2e.connected_machine_harness import (
    connection_row,
    provider_for,
    running_machine,
)

pytestmark = pytest.mark.e2e

_KEY = "test-runtime-credential-key-32-bytes"
_REPO = Path(__file__).resolve().parents[6]
_LAPTOP_IMAGE = "lemma-e2e-laptop:pr3"
_CODE = re.compile(r"code it shows is\s+([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})")


@pytest.fixture(autouse=True)
def machines_on(monkeypatch):
    monkeypatch.setattr(workspace_settings, "machine_connections_enabled", True)
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", f"{_KEY}")
    monkeypatch.setenv("WORKSPACE_RUNTIME_CREDENTIAL_KEY", _KEY)


@pytest.fixture(scope="module")
def built_bundle(tmp_path_factory) -> Path:
    """The runtime bundle the server gives machines, built from this tree."""
    configured = os.getenv("WORKSPACE_RUNTIME_BUNDLE_DIR")
    if configured and (Path(configured) / "manifest.json").is_file():
        return Path(configured)
    out = tmp_path_factory.mktemp("runtime-bundle")
    subprocess.run(
        [sys.executable, "scripts/build_runtime_bundle.py", "--out-dir", str(out)],
        cwd=_REPO / "lemma-backend",
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture
def serving_bundle(built_bundle, monkeypatch):
    monkeypatch.setattr(workspace_settings, "runtime_bundle_dir", f"{built_bundle}")
    bundle_module.runtime_bundle.cache_clear()
    yield
    bundle_module.runtime_bundle.cache_clear()


async def _run_journeys(provider, instance, *, preview_port: int) -> None:
    for journey in machine_journeys.JOURNEYS:
        await journey(provider, instance)
    await machine_journeys.preview(provider, instance, port=preview_port)


#: PR 1's `/machines` journeys (`test_machines_api_e2e`), run unchanged against a
#: connected machine. Two are left out because they are about the default
#: machine itself: that it is listed as `me`, and the deprecated route alias.
PR1_JOURNEYS = (
    "test_a_process_runs_to_its_exit_code_and_its_output",
    "test_a_process_takes_input_and_is_stopped",
    "test_the_stream_socket_carries_output_out_and_input_in",
    "test_files_are_written_read_moved_and_deleted",
    "test_the_browser_profile_cannot_be_read_deleted_or_overwritten",
    "test_files_sync_between_the_machine_and_the_pod",
    "test_cleaning_up_by_scope_and_the_home_needs_a_yes",
)


def _at(ref: str, path: str) -> str:
    """A `/machines/me...` path, addressed to ``ref`` instead."""
    for tail in ("/", ":", ""):
        prefix = f"/machines/me{tail}"
        if path.startswith(prefix) and (tail or path == prefix):
            return f"/machines/{ref}{tail}" + path[len(prefix) :]
    return path


async def _run_pr1_journeys(
    *, ref: str, owner_client, server, fixed_test_user, fixed_test_org, monkeypatch
) -> None:
    """Every journey in PR 1's suite, with `me` meaning the connected machine.

    The journeys are called as written; only where they address the machine
    changes. HTTP goes through a client that rewrites `/machines/me` to
    ``ref``; the stream socket's URL is rewritten the same way on its way
    into `websockets.connect`.
    """
    import inspect

    import websockets

    from app.modules.workspace.tests.e2e import test_machines_api_e2e as pr1

    async def readdress(request: httpx.Request) -> None:
        request.url = request.url.copy_with(path=_at(ref, request.url.path))

    connect = websockets.connect

    def connect_to_ref(url: str, *args, **kwargs):
        scheme, _, rest = url.partition("://")
        host, slash, path = rest.partition("/")
        return connect(f"{scheme}://{host}{_at(ref, slash + path)}", *args, **kwargs)

    monkeypatch.setattr(websockets, "connect", connect_to_ref)
    client = httpx.AsyncClient(
        transport=owner_client._transport,
        base_url=owner_client.base_url,
        headers=owner_client.headers,
        timeout=owner_client.timeout,
        event_hooks={"request": [readdress]},
    )
    arguments = {
        "machine": {"client": client, "server": server},
        "fixed_test_user": fixed_test_user,
        "fixed_test_org": fixed_test_org,
    }
    async with client:
        for name in PR1_JOURNEYS:
            journey = getattr(pr1, name)
            wanted = inspect.signature(journey).parameters
            await journey(**{key: arguments[key] for key in wanted})


async def _wait_ready(owner_client, connection_id: UUID, *, timeout: float) -> dict:
    return await eventually(
        label="the machine is online with its runtime ready",
        probe=lambda: connection_row(owner_client, connection_id),
        done=lambda row: bool(
            row and row["online"] and row["capabilities"].get("runtime_ready")
        ),
        timeout_seconds=timeout,
        interval_seconds=1,
    )


async def _approve_when_shown(read_output, owner_client, *, name: str) -> UUID:
    """Read the code the CLI prints, and approve it as its person would."""
    shown = await eventually(
        label="the CLI shows a code",
        probe=read_output,
        done=lambda output: _CODE.search(output) is not None,
        timeout_seconds=150,
        interval_seconds=0.5,
    )
    code = _CODE.search(shown).group(1)
    approved = await owner_client.post(
        "/machine-connections:pair", json={"code": code, "name": name}
    )
    assert approved.status_code == 201, approved.text
    return UUID(approved.json()["id"])


# --------------------------------------------------------------- host, here


@pytest.mark.asyncio
async def test_host_mode_in_process_passes_the_machine_journeys(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        row = await connection_row(authenticated_client, machine.connection_id)
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(machine.connection_id), name="m")
        await _run_journeys(provider, instance, preview_port=machine_journeys_port())


def machine_journeys_port() -> int:
    from app.modules.workspace.tests.e2e.connected_machine_harness import free_port

    return free_port()


# ---------------------------------------------------- host, in a container


@pytest.fixture(scope="module")
def laptop_image() -> str:
    """A bare Python 3.14 machine with uv and the Lemma CLI from this tree."""
    dockerfile = Path(__file__).with_name("laptop.Dockerfile")
    built = subprocess.run(
        ["docker", "build", "-f", str(dockerfile), "-t", _LAPTOP_IMAGE, str(_REPO)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.fail(f"building the laptop image failed:\n{built.stderr[-3000:]}")
    return _LAPTOP_IMAGE


# Slow: it builds the uv environment from scratch, past the PR lane's budget.
@pytest.mark.slow
@pytest.mark.workspace
@pytest.mark.asyncio
async def test_host_mode_in_a_throwaway_container_passes_the_machine_journeys(
    backend_server,
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    laptop_image,
    serving_bundle,
    monkeypatch,
):
    port = backend_server["host_base_url"].rsplit(":", 1)[1]
    api = f"http://host.docker.internal:{port}"
    container = f"lemma-laptop-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--add-host",
            "host.docker.internal:host-gateway",
            laptop_image,
            "lemma",
            "--base-url",
            api,
            "machine",
            "connect",
            "--yes",
            "--no-browser",
            "--root",
            "/home/user",
        ],
        check=True,
        capture_output=True,
    )

    async def logs() -> str:
        completed = subprocess.run(
            ["docker", "logs", container], capture_output=True, text=True, check=False
        )
        return completed.stdout + completed.stderr

    try:
        connection_id = await _approve_when_shown(logs, authenticated_client, name="vm")
        # Building the environment downloads the image's whole closure.
        row = await _wait_ready(authenticated_client, connection_id, timeout=900)
        # Rooted at /home/user, so PR 1's journeys -- which name that path in
        # the commands they run -- find it on this machine too.
        assert row["home"] == "/home/user"
        assert row["runtime_version"] == bundle_module.runtime_bundle().version
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(connection_id), name="vm")
        await _run_journeys(provider, instance, preview_port=8765)
        await _run_pr1_journeys(
            ref="mine:vm",
            owner_client=authenticated_client,
            server=backend_server,
            fixed_test_user=fixed_test_user,
            fixed_test_org=fixed_test_org,
            monkeypatch=monkeypatch,
        )

        # Asynchronously: this process is also the backend the CLI talks to,
        # and a blocking wait here would stall the handshake it is waiting on.
        disconnected = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container,
            "lemma",
            "--base-url",
            api,
            "machine",
            "disconnect",
            "--root",
            "/home/user",
            "--yes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, said = await asyncio.wait_for(disconnected.communicate(), 120)
        assert disconnected.returncode == 0, said.decode()
        assert b"Could not tell Lemma" not in said, said.decode()
        assert await connection_row(authenticated_client, connection_id) is None
    finally:
        print(await logs())  # noqa: T201 -- the machine's own log, for a failure
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


# ----------------------------------------------------------------- image


@pytest.mark.workspace
@pytest.mark.asyncio
async def test_image_mode_passes_the_machine_journeys_and_serves_a_browser(
    backend_server,
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    serving_bundle,
    tmp_path,
    monkeypatch,
):
    image = os.getenv("WORKSPACE_E2E_IMAGE") or "lemma-workspace:pr3-machines"
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True
    ).returncode:
        pytest.skip(
            f"no {image} image here; build Dockerfile.workspace or set WORKSPACE_E2E_IMAGE"
        )
    root = tmp_path / "root"
    cli = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "--project",
        str(_REPO / "lemma-cli"),
        "lemma",
        "--base-url",
        backend_server["host_base_url"],
        "machine",
        "connect",
        "--image",
        "--image-ref",
        image,
        "--yes",
        "--no-browser",
        "--root",
        str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = bytearray()

    async def pump() -> None:
        assert cli.stdout is not None
        while chunk := await cli.stdout.read(4096):
            output.extend(chunk)

    pumping = asyncio.ensure_future(pump())

    async def read_output() -> str:
        return output.decode(errors="replace")

    container = "lemma-machine-docker-box"
    try:
        connection_id = await _approve_when_shown(
            read_output, authenticated_client, name="docker-box"
        )
        assert await asyncio.wait_for(cli.wait(), 600) == 0, await read_output()
        # The image's floor has no overlay: the server pushes its bundle down
        # a stream, and the machine installs it and restarts its runtime on it.
        version = bundle_module.runtime_bundle().version
        row = await eventually(
            label="the pushed runtime is the one the machine serves",
            probe=lambda: connection_row(authenticated_client, connection_id),
            done=lambda current: bool(
                current
                and current["runtime_version"] == version
                and current["capabilities"].get("runtime_ready")
            ),
            timeout_seconds=240,
            interval_seconds=1,
        )
        assert row["mode"] == "image"
        assert row["home"] == "/home/user"
        assert row["capabilities"]["browser"] is True
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(connection_id), name="box")
        await _run_journeys(provider, instance, preview_port=8765)
        await _run_pr1_journeys(
            ref="mine:docker-box",
            owner_client=authenticated_client,
            server=backend_server,
            fixed_test_user=fixed_test_user,
            fixed_test_org=fixed_test_org,
            monkeypatch=monkeypatch,
        )

        # The browser: the display comes up, Chrome opens a page, and the
        # relay a viewer connects to answers over the link.
        process_id = await machine_journeys.start(
            provider, instance, "start-browser about:blank"
        )
        state, code, said = await machine_journeys.read_until(
            provider, instance, process_id
        )
        assert code == 0, said[-2000:]
        relay = await provider.reach_port(
            instance,
            port=row["capabilities"]["relay_port"],
            deadline_at=machine_journeys.deadline(),
        )
        async with httpx.AsyncClient(
            transport=sandbox_transport(), timeout=10
        ) as client:
            health = await eventually(
                label="the relay answers over the link",
                probe=lambda: client.get(f"{relay.url}/health"),
                done=lambda response: response.status_code == 200,
                retry_exceptions=(httpx.HTTPError,),
            )
        assert health.json()

        disconnected = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--project",
            str(_REPO / "lemma-cli"),
            "lemma",
            "--base-url",
            backend_server["host_base_url"],
            "machine",
            "disconnect",
            "--root",
            str(root),
            "--yes",
        )
        assert await disconnected.wait() == 0
        assert await connection_row(authenticated_client, connection_id) is None
        gone = subprocess.run(
            ["docker", "inspect", container], capture_output=True, check=False
        )
        assert gone.returncode != 0
    finally:
        pumping.cancel()
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        subprocess.run(
            ["docker", "volume", "rm", "-f", f"{container}-home"], capture_output=True
        )
        if cli.returncode is None:
            cli.kill()
        print(json.dumps({"cli": (await read_output())[-4000:]}))  # noqa: T201
