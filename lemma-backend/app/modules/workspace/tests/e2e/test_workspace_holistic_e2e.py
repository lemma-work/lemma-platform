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


#: Two pages, served from inside the sandbox. The link is the page's one
#: focusable element -- reached by `Tab` and activated by `Return`, never by
#: aiming a click at it -- so neither its size nor its position matters to
#: this test at all; `background` gives it a colour nothing else in either
#: page uses only so a human glancing at the pane can still see it.
VIEW_SITE_PORT = 18077
VIEW_SITE = f"http://127.0.0.1:{VIEW_SITE_PORT}"
VIEW_LINK_RGB = (0xE6, 0x7E, 0x22)
#: What `_find_page_point` scans the framebuffer for, to find anywhere on the
#: page to click -- not the link's own colour, the page's. Saturated and far
#: from any of grey, white, or the other two colours here on purpose: a first
#: version used pale, close-together tones (`#cde`/`#edc`, differing only in
#: byte order) that Chrome's own colour management shifts a few units in
#: rendering (dumping raw sample pixels came back `(211,227,253)` for a
#: requested `(204,221,238)`), and that same shift was enough to put
#: `next.html`'s pale background within this test's tolerance of ordinary
#: light-grey browser chrome -- a click and keypresses this test sent in
#: *view* mode, which the relay must silently drop, read as "navigated" only
#: because the colour check the test asserted with matched the browser's own
#: toolbar. A saturated colour has no such near miss to make.
VIEW_BG_RGB = (0x00, 0x96, 0x88)
#: The page navigated to. Nothing here rendering as this, or as `VIEW_BG_RGB`
#: or `VIEW_LINK_RGB`, within tolerance is what makes it trustworthy as
#: "navigation happened" -- not merely "arrived at" a colour used elsewhere.
VIEW_NEXT_BG_RGB = (0x9B, 0x27, 0xB0)
_VIEW_PAGES = f"""set -e
mkdir -p /tmp/lemma-view-site
cat > /tmp/lemma-view-site/index.html <<'HTML'
<html><head><title>Watch me</title></head><body style="margin:0;background:rgb({VIEW_BG_RGB[0]},{VIEW_BG_RGB[1]},{VIEW_BG_RGB[2]})">
<a href="/next.html" style="position:fixed;top:0;right:0;width:40vw;height:100vh;background:rgb({VIEW_LINK_RGB[0]},{VIEW_LINK_RGB[1]},{VIEW_LINK_RGB[2]})"></a>
</body></html>
HTML
cat > /tmp/lemma-view-site/next.html <<'HTML'
<html><head><title>Driven</title></head><body style="margin:0;background:rgb({VIEW_NEXT_BG_RGB[0]},{VIEW_NEXT_BG_RGB[1]},{VIEW_NEXT_BG_RGB[2]})">arrived</body></html>
HTML
setsid nohup python3 -m http.server PORT --directory /tmp/lemma-view-site \
    >/tmp/lemma-view-site.log 2>&1 </dev/null &
"""


class _RfbReader:
    """A buffered byte reader over a WebSocket carrying RFB.

    RFB message boundaries and WebSocket frame boundaries are unrelated here
    -- the relay forwards whatever `websockify` hands it, chunked however
    `websockify`'s own reads from `x11vnc` happened to land -- so a caller
    that needs exactly N bytes cannot assume one `recv()` provides them.
    """

    def __init__(self, socket) -> None:
        self._socket = socket
        self._buf = b""

    async def exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            more = await self._socket.recv()
            self._buf += more if isinstance(more, bytes) else more.encode()
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


class _RfbInfo:
    def __init__(self, width: int, height: int, pixel_format: bytes) -> None:
        self.width = width
        self.height = height
        self.bpp = pixel_format[0]
        self.big_endian = pixel_format[2] != 0
        self.red_max = int.from_bytes(pixel_format[4:6], "big")
        self.green_max = int.from_bytes(pixel_format[6:8], "big")
        self.blue_max = int.from_bytes(pixel_format[8:10], "big")
        self.red_shift = pixel_format[10]
        self.green_shift = pixel_format[11]
        self.blue_shift = pixel_format[12]


async def _rfb_handshake(reader: _RfbReader, socket) -> _RfbInfo:
    """RFB 3.8's handshake, down to the one security type this relay offers.

    `x11vnc` is started with `-nopw` (see `start-browser.sh`), so the only
    security type on offer is 1 (None) -- there is no password this test
    could supply even if it wanted to skip this.
    """
    version = await reader.exact(12)
    assert version.startswith(b"RFB 003."), version
    await socket.send(b"RFB 003.008\n")

    n_types = (await reader.exact(1))[0]
    if n_types == 0:
        reason_len = int.from_bytes(await reader.exact(4), "big")
        reason = await reader.exact(reason_len)
        raise AssertionError(f"security handshake refused: {reason!r}")
    types = await reader.exact(n_types)
    assert 1 in types, f"no None security type on offer: {types!r}"
    await socket.send(bytes([1]))
    result = int.from_bytes(await reader.exact(4), "big")
    assert result == 0, f"security handshake failed with result {result}"

    await socket.send(bytes([1]))  # ClientInit: shared-flag
    # width(2) + height(2) + pixel-format(16) -- the name-length that follows
    # is a separate, fourth field, not part of this one.
    server_init = await reader.exact(20)
    width = int.from_bytes(server_init[0:2], "big")
    height = int.from_bytes(server_init[2:4], "big")
    name_len = int.from_bytes(await reader.exact(4), "big")
    await reader.exact(name_len)
    return _RfbInfo(width, height, server_init[4:20])


async def _request_raw_framebuffer(socket, info: _RfbInfo) -> None:
    """Ask for one full, non-incremental update, in Raw encoding only.

    Raw is the one encoding every RFB server must support, and forcing it
    (rather than accepting whatever `x11vnc` would otherwise choose) keeps
    this test from needing a decoder for Tight, Hextile, or anything else.
    """
    set_encodings = (
        b"\x02\x00" + (1).to_bytes(2, "big") + (0).to_bytes(4, "big", signed=True)
    )
    await socket.send(set_encodings)
    request = (
        b"\x03\x00"
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + info.width.to_bytes(2, "big")
        + info.height.to_bytes(2, "big")
    )
    await socket.send(request)


async def _read_framebuffer(reader: _RfbReader, info: _RfbInfo) -> bytearray:
    """One FramebufferUpdate's rectangles, placed into a full-screen canvas.

    Placed by each rectangle's own `x`/`y` rather than assumed to be one
    rectangle covering the whole request: nothing in the protocol promises
    that, only that the union of what is sent covers what was asked for.
    """
    n = info.bpp // 8
    canvas = bytearray(info.width * info.height * n)
    msg_type = (await reader.exact(1))[0]
    assert msg_type == 0, f"expected a FramebufferUpdate (0), got {msg_type}"
    await reader.exact(1)  # padding
    n_rects = int.from_bytes(await reader.exact(2), "big")
    for _ in range(n_rects):
        header = await reader.exact(12)
        x = int.from_bytes(header[0:2], "big")
        y = int.from_bytes(header[2:4], "big")
        w = int.from_bytes(header[4:6], "big")
        h = int.from_bytes(header[6:8], "big")
        encoding = int.from_bytes(header[8:12], "big", signed=True)
        assert encoding == 0, f"expected Raw encoding (0), got {encoding}"
        row_bytes = w * n
        for row in range(h):
            row_data = await reader.exact(row_bytes)
            offset = ((y + row) * info.width + x) * n
            canvas[offset : offset + row_bytes] = row_data
    return canvas


def _color_planes(canvas: bytes, info: _RfbInfo) -> tuple:
    """The framebuffer as three same-shaped int arrays, one per channel.

    Numpy, not a pure-Python scan: everything built on this runs over up to a
    full 1440x960 framebuffer on every retry, and a per-pixel Python loop over
    that many pixels is slow enough to matter here.
    """
    import numpy as np

    n = info.bpp // 8
    byte_index = {shift: shift // 8 for shift in (0, 8, 16, 24)}
    if info.big_endian:
        byte_index = {shift: (info.bpp - 8 - shift) // 8 for shift in byte_index}

    arr = np.frombuffer(bytes(canvas), dtype=np.uint8).reshape(
        info.height, info.width, n
    )

    def channel(shift: int, maxval: int):
        plane = arr[:, :, byte_index[shift]].astype(np.int32)
        return plane * 255 // maxval if maxval and maxval != 255 else plane

    return (
        channel(info.red_shift, info.red_max),
        channel(info.green_shift, info.green_max),
        channel(info.blue_shift, info.blue_max),
    )


def _find_page_point(
    canvas: bytes,
    *,
    info: _RfbInfo,
    background_rgb: tuple[int, int, int],
    tolerance: int = 40,
) -> tuple[int, int] | None:
    """A point squarely inside the page, found without matching the link at all.

    Squarely inside, not on the link's own edge: this is only ever used to
    click somewhere the page will accept focus, not to click the link itself
    -- the link is activated by keyboard afterwards (`Tab` then `Return`),
    once a click anywhere on the page has given the page's own document
    focus, rather than by aiming at coordinates on it. A first version of
    this test aimed for a point on the link directly, computed from the
    page's own right edge; it proved unreliable to reproduce even after
    widening the link to forty percent of the window and re-deriving that
    edge from the median matching row, and was replaced by this, once it
    was clear that the actual gap was never precision at the pixel this
    test finds so much as focus, which a click anywhere on the document
    settles regardless of exactly where it lands.
    """
    red, green, blue = _color_planes(canvas, info)
    match = (
        (abs(red - background_rgb[0]) <= tolerance)
        & (abs(green - background_rgb[1]) <= tolerance)
        & (abs(blue - background_rgb[2]) <= tolerance)
    )
    import numpy as np

    ys, xs = np.nonzero(match)
    if len(xs) == 0:
        return None
    return int(np.median(xs)), int(np.median(ys))


def _background_fraction(
    canvas: bytes, *, info: _RfbInfo, rgb: tuple[int, int, int], tolerance: int = 40
) -> float:
    """How much of the screen is close to `rgb` -- proof a page arrived.

    A fraction of the whole display, not of Chrome's own window: this test
    never learns the window's bounds, only points on it, so there is nothing
    narrower to divide by. `#cde` and `#edc` each cover most of their own
    page, so "most of the display" and "almost none of it" are the only two
    outcomes either page produces here, comfortably apart at any reasonable
    threshold.
    """
    import numpy as np

    red, green, blue = _color_planes(canvas, info)
    match = (
        (abs(red - rgb[0]) <= tolerance)
        & (abs(green - rgb[1]) <= tolerance)
        & (abs(blue - rgb[2]) <= tolerance)
    )
    return float(np.count_nonzero(match)) / match.size


async def _click(socket, x: int, y: int) -> None:
    """A PointerEvent press and release at `(x, y)`, button-mask bit 0 (left)."""
    for mask in (1, 0):
        await socket.send(
            b"\x05" + bytes([mask]) + x.to_bytes(2, "big") + y.to_bytes(2, "big")
        )


#: X11 keysyms for `_key_press`.
_KEYSYM_TAB = 0xFF09
_KEYSYM_RETURN = 0xFF0D


async def _key_press(socket, keysym: int) -> None:
    """A KeyEvent press and release for `keysym`.

    type(1)=4, down-flag(1), padding(2), keysym(4, big-endian) -- the RFB
    KeyEvent message this relay's own `_KEY_EVENT_MSG` in the unit tests is
    modelled on.
    """
    for down in (1, 0):
        await socket.send(
            b"\x04" + bytes([down]) + b"\x00\x00" + keysym.to_bytes(4, "big")
        )


async def test_a_person_watches_the_agents_browser_and_then_drives_it(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    """The live view, through every layer that carries it.

    Nothing else covers this. The relay is tested against a fake browser, the
    controller against a fake relay, and the pane's own arithmetic went away
    entirely with the move to VNC -- RFB owns rendering and input capture, so
    there is no client-side mapping left for a unit test to protect. What is
    still only proven end to end is that the two ends of the socket really do
    share one screen: a page is opened in the workspace's shared (default)
    browser session, a viewer attaches over VNC to the real display it is
    running on, and a click sent down that same socket lands where that
    browser can see it.

    Opened with `agent-browser` directly, over a shell command, rather than
    through the agent's own `browser_open` tool: that tool scopes every
    conversation to its own named session and profile on purpose (`app/
    modules/workspace/domain/browser_context.py`'s `agent_session` -- so one
    conversation's agent never inherits another's cookies), and `/vnc` has no
    way to name a session at all -- it shows the shared *default* session's
    display, which is what a plain "watch this computer's browser" panel is
    for. A conversation's own agent browsing is a different, not-yet-viewable
    browser entirely; this test is about the shared one.

    It also pins the one rule the relay adds to RFB: a viewer who is only
    watching may not move the mouse or press a key. That refusal is the whole
    difference between "watch the agent work" and "anyone with the socket
    drives".

    VNC shows the shared display whole, not a picture cropped to one page --
    a deliberate limitation of this feature, not a bug -- so this test does
    not know where in the framebuffer Chrome's window actually sits, or
    exactly what colour a flat CSS fill actually renders as there. It works
    around both: a click anywhere on the page (`_find_page_point` locates one
    by the page's own background, not by anything to do with the link) gives
    the document focus, from which `Tab` reaches the link -- the page's only
    focusable element -- and `Return` activates it, so no coordinate ever has
    to land on the link itself. Whether that landed is read off the same
    picture too, by how much of the display now looks like the page navigated
    to -- not by asking Chrome's own DevTools protocol, which answers about
    whichever tab CDP picks first and was, in practice, as often the
    browser's own blank first tab as the page this test opened.
    """
    del configure_workspace_api_url
    import asyncio

    import websockets

    from sandbox_runtime.browser_relay.app import CLOSE_NO_BROWSER

    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    served = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=_VIEW_PAGES.replace("PORT", str(VIEW_SITE_PORT)),
            timeout_seconds=60,
            comment="Serve a page with a distinctly-coloured link",
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

    # A killed-and-restarted browser, not just an opened page: a sandbox this
    # test resumed (its own container reused across a run of this suite, or a
    # prior test in this module) can already have a default-session Chrome
    # open to something else entirely, X11 window stacking under this image's
    # no-window-manager display being what it is -- so a fresh, single window
    # is worth the cost of forcing one rather than trusting whatever was
    # already on screen. `start-browser` starting with a URL is exactly this
    # module's own `test_the_browser_starts_again_after_its_x_server_dies_
    # uncleanly` pattern, and it re-idempotently starts x11vnc/websockify too.
    await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="pkill -9 -x Xvfb 2>/dev/null; pkill -9 chromium 2>/dev/null; sleep 1; true",
            timeout_seconds=30,
            comment="Clear whatever was already on the shared display",
        ),
    )
    opened = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=(
                "AGENT_BROWSER_SESSION=workspace "
                "AGENT_BROWSER_PROFILE=/tmp/lemma-browser/profile "
                f"start-browser {VIEW_SITE}/"
            ),
            timeout_seconds=60,
            comment="Open a page in the shared default session VNC watches",
        ),
    )
    assert opened.success and "Watch me" in (opened.stdout or ""), opened

    # In the query string, which is how the pane authenticates: a browser
    # cannot put a header on a WebSocket handshake, and the desktop app's
    # WKWebView does not carry the cookie to this host at all.
    token = fixed_test_user["token"]
    base = backend_server["host_base_url"].replace("http://", "ws://", 1)

    def view_socket(mode: str) -> str:
        return f"{base}/workspace/browser/view?mode={mode}&access_token={token}"

    async def find_page_point(socket, reader: _RfbReader) -> tuple[int, int, _RfbInfo]:
        """Anywhere on the real page, once Chrome has painted it.

        The first update after a fresh connection can arrive before the
        compositor has painted anything at all -- a blank framebuffer, not a
        broken one -- so this polls a few more requests on the same
        connection rather than trusting the very first one.
        """
        info = await _rfb_handshake(reader, socket)

        async def probe():
            await _request_raw_framebuffer(socket, info)
            canvas = await _read_framebuffer(reader, info)
            return _find_page_point(canvas, info=info, background_rgb=VIEW_BG_RGB)

        found = await eventually(
            label="the page rendering on the shared display",
            probe=probe,
            done=lambda found: found is not None,
            timeout_seconds=10.0,
            interval_seconds=0.5,
        )
        return found[0], found[1], info

    async def connect_and_find_page(mode: str) -> tuple:
        """A connection already past a point on the page, retrying the
        connection itself when the relay was not ready for it yet.

        `open_internal` above returns once Chrome answers a CDP command --
        which can be moments before the relay's own liveness check (a
        recorded-port file, read and then probed independently) sees the same
        browser as up. A `CLOSE_NO_BROWSER` this soon after opening is that
        gap, not a real refusal, so it is worth one more try rather than
        failing on it -- anything else closes the connection and propagates
        immediately, by raising a type `eventually` was not told to retry.
        """

        async def probe():
            socket = await websockets.connect(view_socket(mode), max_size=None)
            reader = _RfbReader(socket)
            try:
                x, y, info = await find_page_point(socket, reader)
            except websockets.exceptions.ConnectionClosedError as exc:
                await socket.close()
                if exc.code == CLOSE_NO_BROWSER:
                    raise
                raise RuntimeError(f"the relay refused this connection: {exc}") from exc
            else:
                return socket, reader, x, y, info

        return await eventually(
            label="a VNC connection the relay is ready to serve",
            probe=probe,
            done=lambda _: True,
            timeout_seconds=45.0,
            interval_seconds=0.5,
            retry_exceptions=(websockets.exceptions.ConnectionClosedError,),
        )

    async def next_page_fraction() -> float:
        """How much of the display now looks like the page navigated to.

        A fresh view-mode connection every call, not a long-lived one reused
        across many polls: some request/response cycle among the dozen or so
        `eventually` below can drive through a connection this test kept open
        left it a message ahead of or behind where `_read_framebuffer` next
        expected to be reading -- reproduced by reusing one connection across
        repeated polls and gone the moment each poll opened its own instead.
        A fresh RFB handshake costs one extra round trip, well inside the
        interval `eventually` already waits between polls.
        """
        socket = await websockets.connect(view_socket("view"), max_size=None)
        try:
            reader = _RfbReader(socket)
            info = await _rfb_handshake(reader, socket)
            await _request_raw_framebuffer(socket, info)
            canvas = await _read_framebuffer(reader, info)
            return _background_fraction(canvas, info=info, rgb=VIEW_NEXT_BG_RGB)
        finally:
            await socket.close()

    watching, _watch_reader, x, y, _view_info = await connect_and_find_page("view")
    try:
        # Watching means watching. This is the relay's own rule -- RFB itself
        # takes input from whoever connects to it, so the refusal has to be
        # behavioral rather than a message: the click and the keys below must
        # not land.
        await _click(watching, x, y)
        await _key_press(watching, _KEYSYM_TAB)
        await _key_press(watching, _KEYSYM_RETURN)
        await asyncio.sleep(2)
        assert await next_page_fraction() < 0.1, (
            "a view-mode click or keypress navigated the page"
        )
    finally:
        await watching.close()

    driving, _drive_reader, x, y, _drive_info = await connect_and_find_page("control")
    try:
        # A click anywhere on the page, not on the link: this test does not
        # know precisely where the link renders (see `_find_page_point`), only
        # that a document click gives the page itself keyboard focus, from
        # which `Tab` reaches its one focusable element -- the link -- and
        # `Return` activates it, same as a person tabbing to a link and
        # pressing enter would.
        await _click(driving, x, y)
        await _key_press(driving, _KEYSYM_TAB)
        await _key_press(driving, _KEYSYM_RETURN)
    finally:
        await driving.close()

    # A bare timeout here says only that the click did not navigate, which is
    # the one thing already known -- but there is no coordinate arithmetic
    # left in this feature for a failure to be diagnosed by, the way the old
    # picture-vs-page numbers used to be needed for.
    await eventually(
        label="the click navigating to the next page",
        probe=next_page_fraction,
        done=lambda fraction: fraction > 0.3,
        timeout_seconds=30,
        interval_seconds=1.0,
    )


async def test_an_agent_can_record_the_browser_and_get_a_playable_file(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """Recording is a capability of the image, so only the image can prove it.

    `agent-browser record` has existed all along and the browser skill has
    documented it with a worked example; it shells out to `ffmpeg`, which the
    workspace image did not install, so every attempt failed and no test
    noticed because nothing ever tried.

    Two things are asserted rather than one, because the second is how this
    fails quietly. `record stop` reports `Recording saved` whether or not a file
    was written -- a relative path is resolved by the browser daemon rather than
    by the caller's shell and lands somewhere nobody looks -- so "the CLI said
    it worked" is not evidence. `ffprobe` reading a video stream out of the file
    is.
    """
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="start-browser about:blank",
            timeout_seconds=120,
            comment="Start the sandbox browser",
        ),
    )
    assert started.success, started

    take = "/workspace/recording-e2e/take.webm"
    recorded = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            # Absolute, deliberately: the daemon resolves this path, not the
            # shell, and a relative one is silently written elsewhere.
            # Every step silenced but the last, so stdout is the codec name and
            # nothing else. Left unsilenced, `agent-browser` prints its own
            # "Recording saved" over the answer being asserted on.
            cmd=(
                f"mkdir -p $(dirname {take}) && "
                f"agent-browser record start {take} >/dev/null && "
                "agent-browser open "
                "'data:text/html,<h1 style=font-size:90px>Lemma</h1>' >/dev/null && "
                "for i in 1 2 3; do sleep 2; agent-browser get url >/dev/null; done && "
                "agent-browser record stop >/dev/null && "
                f"test -s {take} && "
                "ffprobe -v error -select_streams v:0 "
                f"-show_entries stream=codec_name -of csv=p=0 {take}"
            ),
            timeout_seconds=180,
            comment="Record a short browser session and read it back",
        ),
    )

    assert recorded.success, recorded
    assert recorded.exit_code == 0, recorded.stderr
    # A codec name means ffprobe found a video stream, not merely a file.
    assert recorded.stdout.strip() in {"vp8", "vp9"}, recorded.stdout
