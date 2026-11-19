"""A connected machine's agent: its link's deadlines, its runtime, its files.

The socket here is a script of frames, not a server: what the agent does when
Lemma goes quiet, or never answers, is the point, and a real server answers.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runtime.machine_link import agent as machine_agent
from sandbox_runtime.machine_link import mux
from sandbox_runtime.machine_link.agent import MachineAgent, Stop, _Link
from sandbox_runtime.machine_link.config import MachineConfig, write_secret


pytestmark = pytest.mark.unit


class ScriptedSocket:
    """Hands out ``frames`` in order, then is silent for ever."""

    def __init__(self, *frames: dict[str, object]) -> None:
        self._frames = [json.dumps(frame) for frame in frames]
        self.sent: list[object] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakeRuntime:
    """Starts when told to, fails the first ``failures`` times, and can die."""

    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.starts = 0
        self.alive = False

    def installed_version(self) -> str | None:
        return "v1"

    def running(self) -> bool:
        return self.alive

    async def healthy(self) -> bool:
        return self.alive

    async def ensure(self, token: str) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("the runtime exited with 1")
        self.starts += 1
        self.alive = True

    async def stop(self) -> None:
        self.alive = False


def make_agent(tmp_path: Path, runtime: FakeRuntime | None = None) -> MachineAgent:
    secret = tmp_path / "state" / "machine-secret"
    write_secret(secret, "s3cret")
    config = MachineConfig(
        api_url="http://lemma.test",
        connection_id="c1",
        name="laptop",
        mode="host",
        home=str(tmp_path / "home"),
        overlay_root=str(tmp_path / "overlay"),
        python="python",
        installation_id="i1",
        secret_file=str(secret),
    )
    agent = MachineAgent(config)
    agent.runtime = runtime or FakeRuntime()  # type: ignore[assignment]
    return agent


def welcome(heartbeat_ms: int = 20_000) -> dict[str, object]:
    return {
        "type": "welcome",
        "body": {
            "name": "laptop",
            "heartbeat_ms": heartbeat_ms,
            "runtime": {"token": "t", "version": "v1"},
        },
    }


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(machine_agent, "HANDSHAKE_SECONDS", 0.1)
    monkeypatch.setattr(machine_agent, "RUNTIME_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(machine_agent, "RUNTIME_MAX_BACKOFF_SECONDS", 0.02)


async def _until(condition: Callable[[], object], timeout: float = 2) -> None:
    """Wait on a condition, never on the clock: poll it until it holds."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def test_a_server_that_never_welcomes_ends_the_link_attempt(
    tmp_path: Path, quick: None
) -> None:
    link = _Link(make_agent(tmp_path), ScriptedSocket())

    # The link's own deadline, not this test's: the attempt must have ended.
    serving = asyncio.ensure_future(link.serve())
    done, _ = await asyncio.wait({serving}, timeout=2)
    if not done:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
    assert serving in done, "the handshake deadline never fired"
    with pytest.raises(TimeoutError):
        serving.result()


async def test_a_link_that_goes_silent_is_given_up_to_reconnect(
    tmp_path: Path, quick: None
) -> None:
    link = _Link(make_agent(tmp_path), ScriptedSocket(welcome(heartbeat_ms=30)))

    code, reason, _ = await asyncio.wait_for(link.serve(), 2)

    assert (code, reason) == (None, "silent")


async def test_an_unanswered_revoke_is_not_reported_as_done(
    tmp_path: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = ScriptedSocket(welcome())

    class Connect:
        async def __aenter__(self) -> ScriptedSocket:
            return socket

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(machine_agent.websockets, "connect", lambda *a, **k: Connect())
    agent = make_agent(tmp_path)

    with pytest.raises(Stop, match="did not confirm"):
        await asyncio.wait_for(agent.revoke(), 2)


async def test_a_runtime_that_fails_to_start_is_tried_again(
    tmp_path: Path, quick: None
) -> None:
    runtime = FakeRuntime(failures=2)
    link = _Link(make_agent(tmp_path, runtime), ScriptedSocket())

    supervising = asyncio.ensure_future(link._supervise_runtime("t", expected="v1"))
    try:
        await _until(lambda: runtime.starts)
    finally:
        supervising.cancel()
        await asyncio.gather(supervising, return_exceptions=True)

    assert runtime.failures == 0


async def test_a_runtime_that_exits_later_is_started_again(
    tmp_path: Path, quick: None
) -> None:
    runtime = FakeRuntime()
    link = _Link(make_agent(tmp_path, runtime), ScriptedSocket())

    supervising = asyncio.ensure_future(link._supervise_runtime("t", expected="v1"))
    try:
        async with asyncio.timeout(2):
            await _until(lambda: runtime.starts)
            runtime.alive = False
            await _until(lambda: runtime.starts >= 2)
    finally:
        supervising.cancel()
        await asyncio.gather(supervising, return_exceptions=True)

    assert supervising.cancelled()


async def test_a_push_cut_short_leaves_no_partial_archive(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    installs: list[Path] = []

    async def install(**kwargs: object) -> dict[str, object]:
        installs.append(kwargs["archive"])  # type: ignore[arg-type]
        return {"installed": True}

    agent.runtime.install = install  # type: ignore[attr-defined]
    down: asyncio.Queue[bytes] = asyncio.Queue()
    up: asyncio.Queue[bytes] = asyncio.Queue()
    opener = mux.Mux(down.put, role="opener")
    link = _Link(agent, ScriptedSocket())
    link._mux = mux.Mux(up.put, role="acceptor", accept=link._accept)

    async def pump(queue: asyncio.Queue[bytes], into: mux.Mux) -> None:
        while True:
            await into.feed(await queue.get())

    pumps = [
        asyncio.ensure_future(pump(down, link._mux)),
        asyncio.ensure_future(pump(up, opener)),
    ]
    try:
        stream = await opener.open(
            {"kind": "runtime_bundle", "version": "v2", "sha256": "x", "size": 10}
        )
        await stream.write(b"half")
        await _until(lambda: list(agent.config.state_directory.glob("runtime-*.zip")))
        await stream.reset()
        await asyncio.wait_for(link._pushed.wait(), 2)
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        await opener.close()
        await link._mux.close()

    assert not list(agent.config.state_directory.glob("runtime-*.zip"))
    assert installs == []


def test_rewriting_the_secret_makes_a_shared_file_owner_only(tmp_path: Path) -> None:
    secret = tmp_path / "machine-secret"
    secret.write_text("old\n", encoding="utf-8")
    os.chmod(secret, 0o644)

    write_secret(secret, "new")

    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert secret.read_text(encoding="utf-8") == "new\n"
    assert [path.name for path in tmp_path.iterdir()] == ["machine-secret"]
