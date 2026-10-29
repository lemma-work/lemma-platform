"""The relay's half of the faster browser view: presets, the resize lease,
the WebRTC signalling door, and the toolbar's closed command set.

Driven through FastAPI's test client and, for `/rtc`, a real WebSocket server
standing in for the streamer on a real loopback port -- the relay's job there
is to carry text frames and nothing else, so the honest far end is a socket.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect
import websockets

from sandbox_runtime.browser_relay import app as relay_app
from sandbox_runtime.browser_relay import commands, display, rtc
from sandbox_runtime.browser_relay.viewers import VIEWERS, Viewers, viewer_name

TOKEN = "token-abc"
HEADERS = {"X-Lemma-Relay-Token": TOKEN}


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """Each test starts with nobody watching and nobody holding the size."""
    fresh = Viewers()
    monkeypatch.setattr(VIEWERS, "_live", fresh._live)
    monkeypatch.setitem(display._lease, "holder", None)


def _client(monkeypatch, tmp_path) -> TestClient:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    monkeypatch.setattr(relay_app, "TOKEN_PATH", token_file)
    return TestClient(relay_app.create_app())


def _resizes(monkeypatch) -> list[tuple[int, int]]:
    asked: list[tuple[int, int]] = []

    async def fake_resize(width: int, height: int) -> str:
        asked.append((width, height))
        return f"{width}x{height}"

    monkeypatch.setattr(display, "set_display_size", fake_resize)
    return asked


# --- presets ---------------------------------------------------------------


def test_a_preset_is_honoured_even_past_the_resting_size(monkeypatch, tmp_path):
    """Tablet is taller than the resting display; it is a preset, so it is
    what the person asked for rather than something to clamp."""
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1280x800x24")
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="vnc")
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/display:resize",
        json={"width": 820, "height": 1180, "viewer": "me/1"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert asked == [(820, 1180)]


def test_an_older_pane_asking_to_fit_is_clamped_as_before(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1280x800x24")
    asked = _resizes(monkeypatch)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/display:resize", json={"width": 1900, "height": 1100}, headers=HEADERS
    )

    assert response.status_code == 200
    assert asked == [(1280, 800)]


def test_the_presets_are_the_ones_the_frontend_offers(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(display, "current_size", _async_value("1280x800"))

    state = client.get("/display", headers=HEADERS).json()

    assert state["presets"] == {
        "desktop": "1280x800",
        "wide": "1440x900",
        "tablet": "820x1180",
        "phone": "390x844",
    }
    assert state["lease"] is None


def test_the_screen_size_is_read_from_xrandr() -> None:
    sample = "Screen 0: minimum 1 x 1, current 1440 x 900, maximum 1920 x 1200\n"
    assert display.parse_screen_size(sample) == "1440x900"
    assert display.parse_screen_size("nothing useful") is None


# --- the lease -------------------------------------------------------------


def test_a_watcher_cannot_change_the_size(monkeypatch, tmp_path):
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="view", transport="rtc")
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/display:resize",
        json={"width": 390, "height": 844, "viewer": "me/1"},
        headers=HEADERS,
    )

    assert response.status_code == 409
    assert "take control" in response.json()["detail"]
    assert asked == []


def test_a_second_controller_is_told_somebody_has_the_size(monkeypatch, tmp_path):
    """VNC is shared, so two people can be in control at once. The second
    one's choice is refused out loud rather than silently undoing the first's."""
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="vnc")
    VIEWERS.join("me/2", mode="control", transport="vnc")
    client = _client(monkeypatch, tmp_path)
    first = {"width": 1440, "height": 900, "viewer": "me/1"}
    second = {"width": 390, "height": 844, "viewer": "me/2"}

    assert (
        client.post("/display:resize", json=first, headers=HEADERS).status_code == 200
    )
    refused = client.post("/display:resize", json=second, headers=HEADERS)

    assert refused.status_code == 409
    assert "somebody else" in refused.json()["detail"]
    assert asked == [(1440, 900)]


def test_the_lease_goes_with_the_socket(monkeypatch, tmp_path):
    """No goodbye is needed: a holder whose socket closed holds nothing."""
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="vnc")
    VIEWERS.join("me/2", mode="control", transport="vnc")
    client = _client(monkeypatch, tmp_path)
    client.post(
        "/display:resize",
        json={"width": 1440, "height": 900, "viewer": "me/1"},
        headers=HEADERS,
    )

    VIEWERS.leave("me/1")
    response = client.post(
        "/display:resize",
        json={"width": 390, "height": 844, "viewer": "me/2"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert asked[-1] == (390, 844)


def test_giving_back_control_gives_back_the_size(monkeypatch, tmp_path):
    _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="rtc")
    client = _client(monkeypatch, tmp_path)
    client.post(
        "/display:resize",
        json={"width": 1440, "height": 900, "viewer": "me/1"},
        headers=HEADERS,
    )
    assert display.lease_holder() == "me/1"

    VIEWERS.set_mode("me/1", "view")

    assert display.lease_holder() is None


def test_resetting_frees_the_lease(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1280x800x24")
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="vnc")
    client = _client(monkeypatch, tmp_path)
    client.post(
        "/display:resize",
        json={"width": 390, "height": 844, "viewer": "me/1"},
        headers=HEADERS,
    )

    assert client.post("/display:reset", headers=HEADERS).status_code == 200

    assert display.lease_holder() is None
    assert asked[-1] == (1280, 800)


def test_an_older_pane_cannot_resize_under_a_lease(monkeypatch, tmp_path):
    _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="vnc")
    client = _client(monkeypatch, tmp_path)
    client.post(
        "/display:resize",
        json={"width": 390, "height": 844, "viewer": "me/1"},
        headers=HEADERS,
    )

    legacy = client.post(
        "/display:resize", json={"width": 900, "height": 700}, headers=HEADERS
    )

    assert legacy.status_code == 409


# --- the viewer registry ---------------------------------------------------


def test_taking_control_over_webrtc_demotes_the_other_webrtc_viewers() -> None:
    """Mirrors the streamer: one controller, and the newest wins. VNC keeps
    its own modes, because RFB is shared and fixed per socket."""
    viewers = Viewers()
    viewers.join("a", mode="control", transport="rtc")
    viewers.join("b", mode="view", transport="rtc")
    viewers.join("c", mode="control", transport="vnc")

    viewers.set_mode("b", "control")

    assert viewers.mode_of("a") == "view"
    assert viewers.mode_of("b") == "control"
    assert viewers.mode_of("c") == "control"


def test_a_reconnect_does_not_drop_the_viewer_early() -> None:
    viewers = Viewers()
    viewers.join("a", mode="control", transport="vnc")
    viewers.join("a", mode="control", transport="vnc")
    viewers.leave("a")
    assert viewers.mode_of("a") == "control"
    viewers.leave("a")
    assert viewers.mode_of("a") is None
    assert viewers.count() == 0


def test_a_viewer_name_that_could_do_harm_is_replaced() -> None:
    assert viewer_name("me/abc-123") == "me/abc-123"
    assert viewer_name("bad name\nforged log line").startswith("anonymous-")
    assert viewer_name(None).startswith("anonymous-")
    assert viewer_name("x" * 200).startswith("anonymous-")


# --- /rtc ------------------------------------------------------------------


def test_only_the_streamers_word_changes_a_mode() -> None:
    """The API asks; the streamer, having checked a ticket, answers."""
    assert rtc.mode_change(json.dumps({"t": "mode", "mode": "control"})) == "control"
    assert rtc.mode_change(json.dumps({"t": "ready", "mode": "view"})) == "view"
    assert rtc.mode_change(json.dumps({"t": "control", "ticket": "x"})) is None
    assert rtc.mode_change(json.dumps({"t": "mode", "mode": "root"})) is None
    assert rtc.mode_change("not json") is None


def test_an_image_without_a_streamer_says_so(monkeypatch, tmp_path):
    """4427 is "use VNC", which the pane does without telling anyone."""
    monkeypatch.setattr(relay_app, "live_port", _async_value(1234))
    monkeypatch.setattr(rtc, "STREAM_PORT", _free_port())
    monkeypatch.setattr(rtc, "sandbox_command", lambda name: str(tmp_path / name))
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect("/rtc?mode=view", headers=HEADERS) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()

    assert closed.value.code == rtc.CLOSE_NO_STREAMER


def test_rtc_is_behind_the_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/rtc") as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
    assert closed.value.code == relay_app.CLOSE_UNAUTHENTICATED


def test_signalling_reaches_the_streamer_and_its_word_sets_the_mode(
    monkeypatch, tmp_path
):
    """A real WebSocket on a real port plays the streamer: it answers `hello`
    with `ready`, and grants control when asked. The relay carries both ways
    and records the mode the streamer reported, not the one requested."""
    received: list[dict[str, object]] = []

    async def streamer(socket) -> None:
        async for raw in socket:
            message = json.loads(raw)
            received.append(message)
            if message["t"] == "hello":
                await socket.send(json.dumps({"t": "ready", "mode": "view"}))
            elif message["t"] == "control":
                await socket.send(json.dumps({"t": "mode", "mode": "control"}))

    port = _serve_in_background(streamer)
    monkeypatch.setattr(rtc, "STREAM_PORT", port)
    monkeypatch.setattr(relay_app, "live_port", _async_value(1234))
    monkeypatch.setattr(relay_app, "_keepalive_loop", _idle_forever)
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect(
        "/rtc?mode=view&viewer=me/1", headers=HEADERS
    ) as socket:
        socket.send_text(json.dumps({"t": "hello", "ticket": "t"}))
        assert json.loads(socket.receive_text()) == {"t": "ready", "mode": "view"}
        assert VIEWERS.mode_of("me/1") == "view"
        socket.send_text(json.dumps({"t": "control", "ticket": "t2"}))
        assert json.loads(socket.receive_text())["mode"] == "control"
        assert VIEWERS.mode_of("me/1") == "control"

    assert [m["t"] for m in received] == ["hello", "control"]
    assert VIEWERS.mode_of("me/1") is None


def test_a_binary_frame_ends_the_signalling(monkeypatch, tmp_path):
    """Not signalling, and passing it on would make this a tunnel."""

    async def streamer(socket) -> None:
        async for _ in socket:
            pass

    monkeypatch.setattr(rtc, "STREAM_PORT", _serve_in_background(streamer))
    monkeypatch.setattr(relay_app, "live_port", _async_value(1234))
    monkeypatch.setattr(relay_app, "_keepalive_loop", _idle_forever)
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect("/rtc?viewer=me/1", headers=HEADERS) as socket:
        socket.send_bytes(b"\x00\x01")
        with pytest.raises(WebSocketDisconnect):
            socket.receive_text()


# --- the toolbar's commands ------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "opened"),
    [
        ("example.com", "https://example.com"),
        ("https://example.com/a?b=1", "https://example.com/a?b=1"),
        ("localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:8080/x", "http://127.0.0.1:8080/x"),
    ],
)
def test_an_address_is_made_a_web_address(typed: str, opened: str) -> None:
    assert commands.address_for(typed) == opened


@pytest.mark.parametrize(
    "typed",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "chrome://settings",
        "data:text/html,<b>x</b>",
        "view-source:https://example.com",
        "ftp://example.com",
    ],
)
def test_nothing_but_the_web_is_opened(typed: str) -> None:
    with pytest.raises(ValueError):
        commands.address_for(typed)


@pytest.mark.parametrize(
    "body",
    [
        {"command": "evaluate", "expression": "document.cookie"},
        {"command": "navigate"},
        {"command": "activate", "target": "../../json/new"},
        {"command": "close", "target": "x"},
        {"command": "back", "extra": "field"},
    ],
)
def test_the_command_set_is_closed(body: dict[str, object]) -> None:
    adapter = TypeAdapter(commands.BrowserCommand)
    with pytest.raises(ValidationError):
        adapter.validate_python(body)


def test_the_toolbar_is_behind_the_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/browser:command", json={"action": {"command": "back"}})
    assert response.status_code == 401


# --- helpers ---------------------------------------------------------------


def _async_value(value):
    async def answer(*_args, **_kwargs):
        return value

    return answer


async def _idle_forever(*_args, **_kwargs) -> None:
    await asyncio.Event().wait()


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve_in_background(handler) -> int:
    """A WebSocket server on its own loop and thread, for the test's life."""
    ready = threading.Event()
    port: list[int] = []

    def run() -> None:
        async def main() -> None:
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                port.append(server.sockets[0].getsockname()[1])
                ready.set()
                await asyncio.Event().wait()

        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    return port[0]


def test_a_containers_cpus_are_its_quota_not_the_hosts(tmp_path) -> None:
    quota = tmp_path / "cpu.max"
    quota.write_text("200000 100000\n")
    assert display.machine_cpus(quota) == 2.0
    quota.write_text("50000 100000\n")
    assert display.machine_cpus(quota) == 0.5
    quota.write_text("max 100000\n")
    assert display.machine_cpus(quota) == float(__import__("os").cpu_count())


def test_a_preset_is_doubled_on_a_dense_screen(monkeypatch, tmp_path) -> None:
    """Presets are CSS pixels; at density 2 the display is twice them."""
    stamp = tmp_path / "density.active"
    stamp.write_text("2\n")
    monkeypatch.setattr(display, "DENSITY_STAMP", stamp)
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1280x800x24")
    asked = _resizes(monkeypatch)
    VIEWERS.join("me/1", mode="control", transport="rtc")
    client = _client(monkeypatch, tmp_path)

    client.post(
        "/display:resize",
        json={"width": 1440, "height": 900, "viewer": "me/1"},
        headers=HEADERS,
    )
    client.post("/display:reset", headers=HEADERS)

    assert asked == [(2880, 1800), (2560, 1600)]


def test_density_is_one_until_the_browser_says_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(display, "DENSITY_STAMP", tmp_path / "absent")
    assert display.active_density() == 1
