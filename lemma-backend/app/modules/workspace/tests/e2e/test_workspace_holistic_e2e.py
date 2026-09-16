"""Real user journeys through every workspace execution surface."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecutePythonRequest,
    ListProcessesRequest,
    ResizeTerminalRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
    execute_python_internal,
    list_processes_internal,
    resize_terminal_internal,
    terminate_process_internal,
    write_stdin_internal,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]


async def _context(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Holistic Workspace Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="holistic_workspace_e2e",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(cmd="true", timeout_seconds=180),
        )

    await eventually(
        label="real workspace sandbox warmup",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    return ctx


async def test_shell_python_file_and_lemma_cli_round_trip(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=(
                "mkdir -p artifacts && "
                "printf 'workspace-file-proof\\n' > artifacts/proof.txt && "
                "cat artifacts/proof.txt"
            ),
            comment="Create and read a workspace file",
            timeout_seconds=30,
        ),
    )
    assert shell.success, shell
    assert "workspace-file-proof" in (shell.stdout or "")
    assert shell.exit_code == 0

    python_first = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="from pathlib import Path\nvalue = Path('artifacts/proof.txt').read_text().strip()\nprint(value)",
            comment="Read shell output from the shared Python session",
        ),
    )
    assert python_first.success, python_first
    assert "workspace-file-proof" in (python_first.stdout or "")

    python_second = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="value = value.upper()\nprint(value)",
            comment="Prove Python state survives the next tool call",
        ),
    )
    assert python_second.success, python_second
    assert "WORKSPACE-FILE-PROOF" in (python_second.stdout or "")

    python_failure = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="raise RuntimeError('intentional workspace python failure')",
            comment="Return user-code failures as tool results",
        ),
    )
    assert python_failure.success is False
    assert "intentional workspace python failure" in (python_failure.error or "")

    cli = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="lemma --output json profile get",
            comment="Use the installed Lemma CLI from the sandbox",
            timeout_seconds=60,
        ),
    )
    assert cli.success, cli
    assert cli.exit_code == 0
    assert fixed_test_user["email"] in (cli.stdout or ""), cli

    service = WorkspaceSandboxService()
    session = await service.get_session(
        ctx.user_id,
        ctx.pod_id,
        session_id=str(ctx.conversation_id),
        initial_cwd=ctx.get_workspace_cwd(),
        organization_id=ctx.org_id,
        workload_type="agent",
        workload_id=ctx.pod_id,
        workload_name=ctx.agent_name,
        scope=["pod.read", "pod.write"],
    )
    async with session:
        await session.write_file("artifacts/api.txt", b"written through file API")
        assert (
            await session.read_file("artifacts/api.txt") == b"written through file API"
        )

        async def chunks():
            yield b"streamed-"
            yield b"file-proof"

        await session.write_file_stream("artifacts/stream.txt", chunks())
        await session.move_file("artifacts/stream.txt", "artifacts/moved.txt")
        async with session.stream_file("artifacts/moved.txt") as stream:
            assert b"".join([chunk async for chunk in stream]) == b"streamed-file-proof"
        listed = await session.list_files("artifacts")
        assert {item.path.rsplit("/", 1)[-1] for item in listed} >= {
            "proof.txt",
            "api.txt",
            "moved.txt",
        }
        await session.delete_file("artifacts/api.txt")
        with pytest.raises(Exception):
            await session.read_file("artifacts/api.txt")
    await service.close()


async def test_tty_process_input_resize_listing_and_termination(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="printf 'TTY_READY\\n'; cat",
            tty=True,
            cols=100,
            rows=30,
            yield_time_ms=500,
            timeout_seconds=30,
            comment="Start an interactive process",
        ),
    )
    assert started.success, started
    assert started.completed is False
    assert started.process_id
    process_id = started.process_id
    assert "TTY_READY" in (started.stdout or "")

    listed = await list_processes_internal(ctx, ListProcessesRequest())
    assert listed.success, listed
    # The runtime deliberately does not report command lines or terminal shape
    # in its process listing. The successful TTY start/input/resize calls above
    # are the authoritative proof that this is an interactive process.
    assert any(item.process_id == process_id for item in listed.processes)

    resized = await resize_terminal_internal(
        ctx,
        ResizeTerminalRequest(process_id=process_id, cols=140, rows=50),
    )
    assert resized.success, resized
    assert resized.completed is False

    sent = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=process_id,
            chars="tty-input-proof\n",
            yield_time_ms=500,
            comment="Send input to the interactive process",
        ),
    )
    assert sent.success, sent
    assert "tty-input-proof" in (sent.stdout or "")
    assert sent.completed is False

    terminated = await terminate_process_internal(
        ctx,
        TerminateProcessRequest(
            process_id=process_id,
            comment="Terminate the interactive process",
        ),
    )
    assert terminated.success, terminated
    assert terminated.completed is True

    after = await list_processes_internal(ctx, ListProcessesRequest())
    assert after.success, after
    assert not any(
        item.process_id == process_id and not item.completed for item in after.processes
    )


async def test_browser_process_and_signed_access_reach_the_sandbox(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    browser_started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="start-browser https://example.com/",
            timeout_seconds=60,
            comment="Start the sandbox browser and open a page",
        ),
    )
    assert browser_started.success, browser_started
    assert browser_started.exit_code == 0

    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    captured = await web_fetch_internal(
        ctx,
        WebFetchRequest(
            urls=["https://example.com/"],
            render=True,
            out_dir="browser-captures",
            comment="Capture a rendered page through the sandbox browser",
        ),
    )
    assert captured.success, captured
    page = captured.pages[0]
    assert page.success, page.error
    assert page.fetched_with == "browser"
    assert page.files["markdown"].startswith("browser-captures/")
    assert page.preview and "Example Domain" in page.preview

    browser = await authenticated_client.post(
        "/workspace/apps/browser/access",
        json={"ttl_seconds": 300},
    )
    assert browser.status_code == status.HTTP_200_OK, browser.text
    browser_payload = browser.json()
    assert browser_payload["app"] == "browser"
    assert browser_payload["url"].startswith("http")
    assert "/workspace-ports/" in browser_payload["url"]
    assert browser_payload["expires_at"]


async def test_the_browser_starts_again_after_its_x_server_dies_uncleanly(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """A socket file outlives the process that made it, and used to be believed.

    Xvfb removes `/tmp/.X11-unix/X99` when it is asked to stop. When it is
    killed instead, the file stays -- and that is the ordinary case, not the
    exotic one: the container is stopped past its grace period, the Docker
    daemon restarts, the host reboots, quiesce cannot reach the runtime, or the
    fabric is E2B, which pauses a sandbox without running quiesce at all and
    keeps `/tmp` across the pause.

    `start-browser` used to test for that socket and take it as proof of a
    running X server, so in any of those cases it skipped starting Xvfb and
    every browser command in the sandbox died with

        ERROR:ui/ozone/platform/x11/ozone_platform_x11.cc: Missing X server or $DISPLAY

    for the life of the container -- the agent's commands and the relay behind a
    person watching alike, with nothing in the sandbox able to clear it. A
    person came back to an idle workspace, asked an agent to browse, and got a
    browser that could never start again.

    SIGKILL is how the state is reached here because it is the one way to leave
    the socket behind on purpose; the bug is about the file, not about signals.
    """
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    first = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="start-browser https://example.com/ 2>&1 | tail -5",
            timeout_seconds=120,
            comment="Start the sandbox browser",
        ),
    )
    assert first.success and "Example Domain" in (first.stdout or ""), first

    # `-x`, not `-f`: a pattern match would name Xvfb in this very command line
    # and kill the shell running it.
    killed = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=(
                "pkill -9 -x Xvfb; sleep 1; "
                "test -S /tmp/.X11-unix/X99 && echo socket-kept || echo socket-gone; "
                "pgrep -x Xvfb >/dev/null && echo xvfb-up || echo xvfb-down"
            ),
            timeout_seconds=60,
            comment="Kill the X server without letting it clean up",
        ),
    )
    # Asserted rather than assumed: if a future image made Xvfb clean up even on
    # SIGKILL, this test would go on passing while testing nothing at all.
    assert "socket-kept" in (killed.stdout or ""), killed
    assert "xvfb-down" in (killed.stdout or ""), killed

    again = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=(
                "start-browser https://example.com/ 2>&1 | tail -5; "
                "pgrep -x Xvfb >/dev/null && echo xvfb-up || echo xvfb-down"
            ),
            timeout_seconds=120,
            comment="Start the browser again over the stale socket",
        ),
    )
    assert again.success, again
    assert "Example Domain" in (again.stdout or ""), again
    assert "Missing X server" not in (again.stdout or ""), again
    # The page alone would also be satisfied by a headless fallback that renders
    # nothing a viewer could watch, so the X server is asserted separately.
    assert "xvfb-up" in (again.stdout or ""), again


#: Two pages, served from inside the sandbox, with the link in the right-hand
#: fifth of the window.
#:
#: Deliberately not a link that fills the viewport, which is what this started
#: as. A full-window target is hit by any arithmetic at all, and the arithmetic
#: is the part that was wrong. A frame carries two sizes: `metadata.deviceWidth`
#: / `deviceHeight`, which is the page, and the JPEG's own pixels, which is the
#: picture -- and they differ, measured, as 1280x720 against 985x800, because
#: the stream encodes within the caps the image sets. Input goes in the
#: *picture's* space, because the stream server scales it back out of the frame
#: itself. Aiming with the page's numbers sends x=1242 at a picture 985 wide,
#: which lands past its right edge and clicks nothing -- and a click that hits
#: no element is indistinguishable from input that never arrived.
VIEW_SITE_PORT = 18077
VIEW_SITE = f"http://127.0.0.1:{VIEW_SITE_PORT}"
#: Where the link starts, as a fraction of the width.
VIEW_LINK_FROM = 0.8
#: Where the test clicks, as a fraction of the picture's width.
VIEW_CLICK_AT = 0.97
_VIEW_PAGES = """set -e
mkdir -p /tmp/lemma-view-site
cat > /tmp/lemma-view-site/index.html <<'HTML'
<html><head><title>Watch me</title></head><body style="margin:0;background:#cde">
<a href="/next.html" style="position:fixed;top:0;right:0;width:20vw;height:100vh;background:#9ab">right edge</a>
</body></html>
HTML
cat > /tmp/lemma-view-site/next.html <<'HTML'
<html><head><title>Driven</title></head><body style="margin:0;background:#edc">arrived</body></html>
HTML
setsid nohup python3 -m http.server PORT --directory /tmp/lemma-view-site \
    >/tmp/lemma-view-site.log 2>&1 </dev/null &
"""


def _jpeg_size(raw: bytes) -> tuple[int, int]:
    """The picture's real pixel size, read out of its own header.

    Not `metadata.deviceWidth`: that is the size of the *page*, and the two are
    different numbers whenever the stream has encoded within its caps -- which
    is the ordinary case, not an edge one. Parsed here rather than with an
    imaging library so this test needs nothing the backend does not already
    have.
    """
    at = 2  # past the start-of-image marker
    while at < len(raw):
        if raw[at] != 0xFF:
            raise AssertionError(f"not a JPEG segment at byte {at}")
        marker = raw[at + 1]
        # SOF0..SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(raw[at + 5 : at + 7], "big")
            width = int.from_bytes(raw[at + 7 : at + 9], "big")
            return width, height
        at += 2 + int.from_bytes(raw[at + 2 : at + 4], "big")
    raise AssertionError("no frame header in this JPEG")


async def _first(socket, kind: str, *, timeout: float = 30.0):
    """The next message of one kind, acking frames on the way past.

    Ack pacing means the stream sends one frame and then waits, so a reader that
    skips frames without acknowledging them stops the stream dead and then times
    out waiting for the message it actually came for.
    """
    import asyncio
    import json

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
        if message.get("type") == kind:
            return message
        if message.get("type") == "frame":
            await socket.send(json.dumps({"type": "ack", "seq": message.get("seq")}))
    raise AssertionError(f"no {kind!r} message arrived within {timeout}s")


async def test_a_person_watches_the_agents_browser_and_then_drives_it(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    """The live view, through every layer that carries it.

    Nothing else covers this. The relay is tested against a fake browser, the
    viewer's arithmetic in jsdom, and the controller against a fake relay -- and
    the feature shipped with the pane attached to a *different browser* than the
    one the agent was using, which every one of those suites was happy with.

    So this runs the real path end to end: the agent's own browser tool opens a
    page, a socket is opened against the API the way the pane opens it, and a
    real JPEG of that page comes back. Then it takes control and clicks, and the
    page navigates -- which is only possible if the input reached the same
    Chrome the picture came from.

    It also pins the one rule the relay adds to `agent-browser`'s protocol: a
    viewer who is watching may not type. That refusal is the whole difference
    between "watch the agent work" and "anyone with the socket drives".
    """
    del configure_workspace_api_url
    import base64
    import json

    import websockets

    from app.modules.agent.tools.browser.browser import open_internal
    from app.modules.agent.tools.browser.models import BrowserOpenRequest

    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    served = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=_VIEW_PAGES.replace("PORT", str(VIEW_SITE_PORT)),
            timeout_seconds=60,
            comment="Serve a page with a full-window link",
        ),
    )
    assert served.success, served

    async def site_answers():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(
                cmd=f"curl -sf -o /dev/null -w '%{{http_code}}' {VIEW_SITE}/",
                timeout_seconds=20,
            ),
        )

    await eventually(
        label="the page being served",
        probe=site_answers,
        done=lambda result: "200" in (result.stdout or ""),
        timeout_seconds=60,
        interval_seconds=1.0,
    )

    # Opened with the agent's own tool, in the agent's own session. The viewer
    # below names only the conversation, so if the two ever stop agreeing about
    # which browser that is, the frame will be of the wrong one -- or of nothing.
    opened = await open_internal(ctx, BrowserOpenRequest(url=f"{VIEW_SITE}/"))
    assert opened.success, opened

    # In the query string, which is how the pane authenticates: a browser
    # cannot put a header on a WebSocket handshake, and the desktop app's
    # WKWebView does not carry the cookie to this host at all.
    token = fixed_test_user["token"]
    base = backend_server["host_base_url"].replace("http://", "ws://", 1)

    def view_socket(mode: str) -> str:
        return (
            f"{base}/workspace/browser/view?mode={mode}"
            f"&conversation={ctx.conversation_id}&access_token={token}"
        )

    async with websockets.connect(view_socket("view"), max_size=None) as watching:
        frame = await _first(watching, "frame")
        picture = base64.b64decode(frame["data"])
        # A JPEG, not an empty string dressed up as one: the two bytes are the
        # start-of-image marker, and a black or absent screen would still have
        # them, so the size floor is what says something was actually drawn.
        assert picture[:2] == b"\xff\xd8", picture[:16]
        assert len(picture) > 2_000, len(picture)
        assert frame["metadata"]["deviceWidth"] > 0

        # Watching means watching. This is the relay's own rule -- the stream
        # server itself takes input from whoever connects to it.
        await watching.send(
            json.dumps(
                {
                    "type": "input_mouse",
                    "eventType": "mousePressed",
                    "x": 10,
                    "y": 10,
                    "button": "left",
                    "buttons": 1,
                    "clickCount": 1,
                }
            )
        )
        refusal = await _first(watching, "error")
        assert refusal["code"] == "read_only", refusal

    async with websockets.connect(view_socket("control"), max_size=None) as driving:
        first = await _first(driving, "frame")
        await driving.send(json.dumps({"type": "ack", "seq": first.get("seq")}))

        page_width = int(first["metadata"]["deviceWidth"])
        page_height = int(first["metadata"]["deviceHeight"])
        picture_width, picture_height = _jpeg_size(base64.b64decode(first["data"]))
        assert picture_width and picture_height

        # In the picture's pixels, which is the only space input is ever in.
        aimed = (
            round(picture_width * VIEW_CLICK_AT),
            round(picture_height / 2),
        )
        assert aimed[0] >= picture_width * VIEW_LINK_FROM, aimed
        # The same aim expressed against the page would be off the picture
        # entirely. Asserted so that a future frame whose two sizes happen to
        # agree says so, rather than passing while proving nothing.
        assert page_width * VIEW_CLICK_AT > picture_width, (
            "the picture is no smaller than the page here, so this no longer "
            f"distinguishes the two spaces ({picture_width} vs {page_width})"
        )
        for event in ("mousePressed", "mouseReleased"):
            await driving.send(
                json.dumps(
                    {
                        "type": "input_mouse",
                        "eventType": event,
                        "x": aimed[0],
                        "y": aimed[1],
                        "button": "left",
                        "buttons": 1,
                        "clickCount": 1,
                    }
                )
            )
        navigated = await _first(driving, "url")
        assert navigated["url"].endswith("/next.html"), (
            f"{navigated} -- aimed at {aimed} on a {page_width}x{page_height} page "
            f"from a {picture_width}x{picture_height} picture"
        )
