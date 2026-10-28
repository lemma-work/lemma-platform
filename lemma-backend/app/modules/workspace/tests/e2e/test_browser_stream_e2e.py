"""The live browser view end to end: TigerVNC without deflate, presets and the
resize lease, and WebRTC through a real TURN relay, on a real Docker workspace.

The WebRTC viewer is `streamcheck` (`sandbox-images/streamer/cmd/streamcheck`),
a headless Pion client that connects the way the web app does -- through the
API's view socket with `transport=rtc`, receiving its ICE servers from the
API and never holding a ticket -- and prints what it got as one JSON line. It
runs in a container on the Docker host, and the media between it and the
sandbox's streamer goes through a coturn container with both ends relay-only,
which is the only shape production allows.

What each test pins:

- the VNC path negotiates no permessage-deflate on either leg the API owns,
  and the server behind it is TigerVNC;
- the screen takes a preset only from the viewer in control, and a second
  controller is told who holds it;
- a watcher gets video and no input channel; a controller gets both; a
  watcher who takes control is upgraded on the same connection;
- with TURN unreachable WebRTC does not connect, and the same machine still
  serves VNC -- which is what the pane falls back to;
- the toolbar's commands steer the browser and refuse a `javascript:` address.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
import websockets
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.runtime import browser_stream_settings_overridden

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]

REPO = Path(__file__).resolve().parents[6]
STREAMER = REPO / "lemma-backend" / "sandbox-images" / "streamer"
COTURN_IMAGE = "coturn/coturn:4.7.0"
TURN_SECRET = "e2e-turn-secret"
PAGE_PORT = 4848


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def streamcheck() -> Path:
    """The headless viewer, built for whatever the Docker host runs.

    From the streamer's own Dockerfile, whose `out` stage also runs the Go
    tests: a viewer built from a streamer that fails its own tests would
    measure nothing worth having.
    """
    out = Path(tempfile.mkdtemp(prefix="streamcheck-"))
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(STREAMER / "Dockerfile"),
            "--target",
            "out",
            "-o",
            f"type=local,dest={out}",
            str(STREAMER),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(f"streamcheck did not build:\n{build.stderr[-4000:]}")
    yield out / "streamcheck"
    shutil.rmtree(out, ignore_errors=True)


@pytest.fixture
def coturn():
    """A relay-only TURN server, published on the Docker host.

    Both the sandbox and the viewer reach it at `host.docker.internal`; the
    relayed media never leaves the coturn container, because both ends'
    candidates are relay addresses on it.
    """
    port = _free_port()
    name = f"lemma-e2e-turn-{uuid4().hex[:8]}"
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"{port}:3478/udp",
            "-p",
            f"{port}:3478/tcp",
            COTURN_IMAGE,
            "-n",
            "--log-file=stdout",
            "--listening-port=3478",
            "--realm=lemma.test",
            "--use-auth-secret",
            f"--static-auth-secret={TURN_SECRET}",
            "--no-tls",
            "--no-dtls",
            "--fingerprint",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.fail(f"coturn did not start: {started.stderr}")
    try:
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


@pytest.fixture
def webrtc_for(fixed_test_user, coturn):
    """This user in the WebRTC rollout, with the e2e coturn as the relay.

    A function the test may call again with another TURN port, which is how
    the forced-ICE-failure case points both ends at a relay that is not there.
    """
    with contextlib.ExitStack() as stack:

        def configure(*, turn_port: int = coturn) -> None:
            stack.enter_context(
                browser_stream_settings_overridden(
                    turn_provider="coturn",
                    coturn_urls=[
                        f"turn:host.docker.internal:{turn_port}?transport=udp",
                        f"turn:host.docker.internal:{turn_port}?transport=tcp",
                    ],
                    coturn_secret=SecretStr(TURN_SECRET),
                    browser_stream="auto",
                    browser_stream_users=[fixed_test_user["id"]],
                )
            )

        configure()
        yield configure


async def _workspace(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Stream e2e {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(response.json()["id"]),
        conversation_id=uuid4(),
        agent_name="stream_e2e",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx, ExecCommandRequest(cmd="true", timeout_seconds=180)
        )

    await eventually(
        label="the workspace sandbox starting",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    return ctx


async def _run(ctx: BaseAgentContext, cmd: str, comment: str, timeout: int = 60):
    result = await exec_command_internal(
        ctx, ExecCommandRequest(cmd=cmd, timeout_seconds=timeout, comment=comment)
    )
    assert result.success, result
    return result


async def _open_a_scrolling_page(ctx: BaseAgentContext) -> None:
    """Something that keeps changing, so frames keep coming."""
    await _run(
        ctx,
        "mkdir -p /tmp/stream-e2e && cd /tmp/stream-e2e && cat > index.html <<'EOF'\n"
        "<!doctype html><title>Stream e2e</title><body style='margin:0'>"
        "<canvas id=c width=1280 height=800></canvas><script>"
        "const x=document.getElementById('c').getContext('2d');let n=0;"
        "(function f(){x.fillStyle=`hsl(${n++%360},70%,50%)`;x.fillRect(0,0,1280,800);"
        "requestAnimationFrame(f)})()</script>\nEOF\n"
        f"(setsid nohup python3 -m http.server {PAGE_PORT} >/dev/null 2>&1 </dev/null &) ; "
        f"for i in $(seq 50); do curl -sf -o /dev/null http://127.0.0.1:{PAGE_PORT}/ && exit 0; sleep 0.1; done; exit 1",
        "Serve a page that repaints every frame",
    )
    opened = await _run(
        ctx,
        f"agent-browser open http://127.0.0.1:{PAGE_PORT}/",
        "Open it in the browser the live view shows",
    )
    assert "Stream e2e" in (opened.stdout or ""), opened


async def _streamcheck(
    binary: Path, image: str, api: str, token: str, *args: str
) -> dict:
    """Run the viewer in a container on the Docker host; its JSON result.

    Awaited, never `subprocess.run`: the API under test serves on this same
    event loop, and a blocking wait would leave the viewer dialling a server
    that cannot answer.
    """
    host_api = api.replace("127.0.0.1", "host.docker.internal").replace(
        "localhost", "host.docker.internal"
    )
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-v",
        f"{binary}:/streamcheck:ro",
        "--entrypoint",
        "/streamcheck",
        image,
        "-api",
        host_api,
        "-token",
        token,
        "-json",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), timeout=120)
    lines = [line for line in out.decode().splitlines() if line.startswith("{")]
    assert lines, f"streamcheck printed nothing:\n{out.decode()}\n{err.decode()}"
    return json.loads(lines[-1])


def _ws_base(backend_server) -> str:
    return backend_server["host_base_url"].replace("http://", "ws://", 1)


async def test_the_vnc_path_is_tigervnc_without_deflate(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _workspace(authenticated_client, fixed_test_org, fixed_test_user)
    await _open_a_scrolling_page(ctx)
    token = fixed_test_user["token"]
    url = f"{_ws_base(backend_server)}/machines/me/browser/view?mode=view&viewer=e2e1&access_token={token}"

    # The browser's leg: offered deflate, as every browser does, and refused.
    async with websockets.connect(url, max_size=None, compression="deflate") as socket:
        assert "permessage-deflate" not in (
            socket.response.headers.get("Sec-WebSocket-Extensions") or ""
        )
        banner = await asyncio.wait_for(socket.recv(), timeout=30)
        assert bytes(banner).startswith(b"RFB 003.008")

    server = await _run(
        ctx, "pgrep -x X0tigervnc && ! pgrep -x x11vnc", "Which VNC server is running"
    )
    assert server.stdout.strip()

    # The relay's leg, asked from inside the sandbox the way the API dials it.
    relay = await _run(
        ctx,
        "/opt/lemma-python/bin/python - <<'EOF'\n"
        "import asyncio, websockets\n"
        "async def main():\n"
        "    token = open('/tmp/lemma-relay/token').read().strip()\n"
        "    async with websockets.connect('ws://127.0.0.1:4850/vnc?mode=view',\n"
        "        additional_headers={'X-Lemma-Relay-Token': token}) as ws:\n"
        "        print(ws.response.headers.get('Sec-WebSocket-Extensions') or 'none')\n"
        "asyncio.run(main())\nEOF",
        "Ask the relay whether it negotiates deflate",
    )
    assert relay.stdout.strip().splitlines()[-1] == "none"


async def test_only_the_controller_sets_the_screen_size(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _workspace(authenticated_client, fixed_test_org, fixed_test_user)
    await _open_a_scrolling_page(ctx)
    token = fixed_test_user["token"]
    base = f"{_ws_base(backend_server)}/machines/me/browser/view"

    async with (
        websockets.connect(
            f"{base}?mode=control&viewer=first&access_token={token}"
        ) as first,
        websockets.connect(
            f"{base}?mode=control&viewer=second&access_token={token}"
        ) as second,
        websockets.connect(
            f"{base}?mode=view&viewer=watcher&access_token={token}"
        ) as watcher,
    ):
        for socket in (first, second, watcher):
            await asyncio.wait_for(socket.recv(), timeout=30)

        async def resize(viewer: str, width: int, height: int) -> dict:
            response = await authenticated_client.post(
                "/machines/me/browser:display",
                json={"width": width, "height": height, "viewer": viewer},
            )
            assert response.status_code == 200, response.text
            return response.json()

        assert (await resize("watcher", 390, 844))["size"] is None
        assert (await resize("first", 820, 1180))["size"] == "820x1180"
        refused = await resize("second", 390, 844)
        assert refused["size"] is None
        assert "somebody else" in refused["reason"]

        details = (await authenticated_client.get("/machines/me/browser")).json()
        assert details["size"] == "820x1180"
        assert details["lease"].endswith(":first")
        assert details["presets"]["tablet"] == "820x1180"
        assert {w["mode"] for w in details["watching"]} == {"control", "view"}

    # Every viewer gone: the screen goes back to its resting size.
    async def resting():
        return (await authenticated_client.get("/machines/me/browser")).json()

    await eventually(
        label="the display going back to its resting size",
        probe=resting,
        done=lambda found: (
            found.get("size") == "1280x800" and found.get("lease") is None
        ),
        timeout_seconds=30,
        interval_seconds=1.0,
    )


# Builds the streamer path end to end through a real coturn: past the PR
# lane's 45 s budget, so it runs in the scheduled protected lane.
@pytest.mark.slow
async def test_webrtc_watch_drive_and_take_control_through_turn(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    streamcheck,
    webrtc_for,
    workspace_image,
):
    del configure_workspace_api_url, webrtc_for
    ctx = await _workspace(authenticated_client, fixed_test_org, fixed_test_user)
    await _open_a_scrolling_page(ctx)
    api = backend_server["host_base_url"]
    token = fixed_test_user["token"]

    status_now = (await authenticated_client.get("/machines/me/browser")).json()
    assert status_now["transport"] == "auto"

    watcher = await _streamcheck(
        streamcheck,
        workspace_image,
        api,
        token,
        "-mode",
        "view",
        "-seconds",
        "4",
        "-try-input",
        "-relay-only",
    )
    assert watcher["connected"], watcher
    assert watcher["selected_pair"].startswith("relay"), watcher
    assert watcher["frames"] >= 30, watcher
    assert watcher["input_channel"] != "open", watcher
    assert watcher["codec"] == "h264", watcher

    driver = await _streamcheck(
        streamcheck,
        workspace_image,
        api,
        token,
        "-mode",
        "control",
        "-seconds",
        "3",
        "-try-input",
        "-relay-only",
    )
    assert driver["connected"] and driver["input_channel"] == "open", driver

    upgraded = await _streamcheck(
        streamcheck,
        workspace_image,
        api,
        token,
        "-mode",
        "view",
        "-seconds",
        "4",
        "-take",
        "-relay-only",
    )
    assert upgraded["mode_after_take"] == "control", upgraded

    vp8 = await _streamcheck(
        streamcheck,
        workspace_image,
        api,
        token,
        "-mode",
        "view",
        "-seconds",
        "3",
        "-codec",
        "vp8",
        "-relay-only",
    )
    assert vp8["codec"] == "vp8" and vp8["frames"] >= 20, vp8

    used = await _run(
        ctx, "pgrep -x lemma-streamer >/dev/null && echo running", "Is the streamer up"
    )
    assert "running" in used.stdout


# Shares the streamcheck build (~80 s) with the test above: slow lane too.
@pytest.mark.slow
async def test_unreachable_turn_fails_webrtc_and_vnc_still_serves(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    streamcheck,
    webrtc_for,
    workspace_image,
):
    del configure_workspace_api_url
    webrtc_for(turn_port=_free_port())
    ctx = await _workspace(authenticated_client, fixed_test_org, fixed_test_user)
    await _open_a_scrolling_page(ctx)
    token = fixed_test_user["token"]

    failed = await _streamcheck(
        streamcheck,
        workspace_image,
        backend_server["host_base_url"],
        token,
        "-mode",
        "view",
        "-seconds",
        "6",
        "-relay-only",
    )
    assert not failed["connected"], failed
    # Reached the API and the streamer, and failed only at ICE: a viewer that
    # never got through would pass the line above for the wrong reason.
    assert "dial" not in (failed.get("error") or ""), failed
    assert failed.get("mode") == "view", failed
    assert failed["frames"] == 0, failed

    # What the pane does next: the same machine, over VNC.
    url = f"{_ws_base(backend_server)}/machines/me/browser/view?mode=view&access_token={token}"
    async with websockets.connect(url, max_size=None) as vnc:
        banner = await asyncio.wait_for(vnc.recv(), timeout=30)
        assert bytes(banner).startswith(b"RFB 003.008")


async def test_the_toolbar_steers_the_browser_and_refuses_script(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    del backend_server, configure_workspace_api_url
    ctx = await _workspace(authenticated_client, fixed_test_org, fixed_test_user)
    await _open_a_scrolling_page(ctx)

    async def command(action: dict) -> dict:
        response = await authenticated_client.post(
            "/machines/me/browser:command", json={"action": action}
        )
        return {"status": response.status_code, **(response.json() or {})}

    listed = await command({"command": "tabs"})
    assert listed["status"] == 200, listed
    assert any(str(PAGE_PORT) in tab["url"] for tab in listed["tabs"])

    went = await command(
        {"command": "navigate", "url": f"127.0.0.1:{PAGE_PORT}/?moved=1"}
    )
    assert went["status"] == 200, went
    assert any("moved=1" in tab["url"] for tab in went["tabs"]), went

    back = await command({"command": "back"})
    assert back["status"] == 200, back

    refused = await command({"command": "navigate", "url": "javascript:alert(1)"})
    assert refused["status"] in (409, 422), refused
    unknown = await authenticated_client.post(
        "/machines/me/browser:command",
        json={"action": {"command": "evaluate", "expression": "1"}},
    )
    assert unknown.status_code == 422

    beacon = await authenticated_client.post(
        "/machines/me/browser:beacon",
        json={"transport": "webrtc", "seconds": 12.5, "fps": 29.8, "codec": "h264"},
    )
    assert beacon.status_code == 204
