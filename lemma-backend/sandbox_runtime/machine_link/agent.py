"""A connected machine's agent: its link to Lemma, its runtime, its streams.

Started by ``lemma machine connect`` (host mode: on the machine itself, as the
person's OS user; image mode: inside the ``lemma-workspace`` container). It
keeps one outbound WebSocket to ``/agent-host/link`` in the ``compute`` role
for as long as it runs, and on it:

* says ``hello`` with the machine's report and is given its runtime's
  credential in ``welcome``; starts the runtime on loopback with it;
* sends ``control`` every heartbeat, carrying the report;
* accepts every stream Lemma opens -- a loopback port, spliced; or a pushed
  runtime overlay, installed, after which the runtime restarts on it;
* reconnects after anything but a close that says to stop. The close codes
  mean what they mean to an Agent Host (docs/architecture/agent-host.md#close-codes):
  4401 three times in a row is a revoked machine and the agent exits; 4426 is
  an agent too old; 4409 is a newer link for this machine, taken back at once
  only when this one had lasted, so two agents holding one secret back off
  instead of trading the link for ever.

Run ``python -m sandbox_runtime.machine_link.agent --config <machine.json>``;
``--revoke`` disconnects the machine from Lemma and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import random
import signal
import sys
import tempfile
from pathlib import Path

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from sandbox_runtime.machine_link import mux
from sandbox_runtime.machine_link.config import (
    PROTOCOL_VERSION,
    MachineConfig,
    platform_name,
)
from sandbox_runtime.machine_link.runtime_process import (
    MAX_BUNDLE_BYTES,
    InstallFailed,
    RuntimeProcess,
)

logger = logging.getLogger("lemma.machine")

REVOKED_OR_MISSING = 4401
SUPERSEDED = 4409
UPGRADE_REQUIRED = 4426
RESTARTING = 1012
NORMAL = 1000

#: Three refusals of the secret in a row is a machine that was removed.
REVOKED_STRIKES = 3
#: A link that lasted this long before being superseded was a hand-over.
HANDOVER_AFTER_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 300.0

AGENT_RELEASE = "machine-agent/1"


class Stop(Exception):
    """The agent must not reconnect; ``message`` says why, for the person."""


class MachineAgent:
    def __init__(self, config: MachineConfig) -> None:
        self.config = config
        self.runtime = RuntimeProcess(config)
        self._token: str | None = None
        self._revoked_strikes = 0
        self._superseded_quickly = 0
        self._next_id = 0

    # --------------------------------------------------------------- reporting

    def capabilities(self) -> dict[str, bool]:
        image = self.config.mode == "image"
        docker = Path("/var/run/docker.sock").exists()
        return {
            "processes": True,
            "files": True,
            "python": True,
            "browser": image,
            "docker": docker,
            "previews": True,
        }

    async def report(self) -> dict[str, object]:
        return {
            "mode": self.config.mode,
            "platform": platform_name(),
            "home": self.config.home,
            "runtime_version": self.runtime.installed_version(),
            "runtime_ready": await self.runtime.healthy(),
            "runtime_port": self.config.runtime_port,
            "relay_port": self.config.relay_port,
            "capabilities": self.capabilities(),
        }

    def _hello(self) -> dict[str, object]:
        return {
            "installation_id": self.config.installation_id,
            "host_release": AGENT_RELEASE,
            "protocol_version": PROTOCOL_VERSION,
        }

    def _frame_id(self) -> str:
        self._next_id += 1
        return f"m{self._next_id}"

    # ------------------------------------------------------------------ links

    async def run(self) -> None:
        """Hold the link until something says to stop."""
        backoff = 0.5
        while True:
            opened_at = asyncio.get_running_loop().time()
            code, reason, after = await self._one_link()
            lasted = asyncio.get_running_loop().time() - opened_at
            delay = self._after_close(code, reason, lasted, after)
            if delay is None:
                backoff = 0.5
                continue
            if delay < 0:
                delay = backoff
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            logger.info(
                "link closed (%s %s); reconnecting in %.1fs", code, reason, delay
            )
            await asyncio.sleep(delay + random.uniform(0, delay / 10))

    def _after_close(
        self, code: int | None, reason: str, lasted: float, after_ms: int | None
    ) -> float | None:
        """How long to wait before the next link: None now, < 0 backoff."""
        if code == REVOKED_OR_MISSING:
            self._revoked_strikes += 1
            if self._revoked_strikes >= REVOKED_STRIKES:
                raise Stop(
                    "This machine was disconnected from Lemma. "
                    "Run `lemma machine connect` to join it again."
                )
            return -1
        self._revoked_strikes = 0
        if code == UPGRADE_REQUIRED:
            raise Stop(
                "This machine's agent is too old for the server; update the Lemma CLI."
            )
        if code == NORMAL and reason == "revoked":
            raise Stop("Disconnected.")
        if code == SUPERSEDED:
            if lasted >= HANDOVER_AFTER_SECONDS:
                self._superseded_quickly = 0
                return None
            self._superseded_quickly += 1
            return min(0.5 * 2**self._superseded_quickly, MAX_BACKOFF_SECONDS)
        if code == RESTARTING and after_ms is not None:
            return after_ms / 1000
        return -1

    async def _one_link(self) -> tuple[int | None, str, int | None]:
        headers = {"Authorization": f"Bearer {self.config.secret()}"}
        try:
            async with websockets.connect(
                self.config.link_url,
                additional_headers=headers,
                compression=None,
                max_size=2 * 1024 * 1024,
                ping_interval=None,
            ) as socket:
                link = _Link(self, socket)
                return await link.serve()
        except ConnectionClosed as closed:
            received = closed.rcvd
            return (
                received.code if received else None,
                received.reason if received else "",
                None,
            )
        except (OSError, InvalidStatus, TimeoutError) as error:
            logger.warning("could not reach Lemma: %s", error)
            return None, type(error).__name__, None

    async def revoke(self) -> None:
        """``lemma machine disconnect``: remove this machine from Lemma."""
        headers = {"Authorization": f"Bearer {self.config.secret()}"}
        async with websockets.connect(
            self.config.link_url, additional_headers=headers, compression=None
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "id": self._frame_id(),
                        "body": {
                            "role": "compute",
                            "hello": self._hello(),
                            "machine": await self.report(),
                        },
                    }
                )
            )
            await _expect(socket, "welcome")
            await socket.send(
                json.dumps({"type": "revoke", "id": self._frame_id(), "body": {}})
            )
            await _expect(socket, "revoked")

    # ----------------------------------------------------------- the runtime

    async def fetch(self, path: str) -> bytes:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                f"{self.config.api_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {self.config.secret()}"},
            )
        response.raise_for_status()
        return response.content


async def _expect(socket, frame_type: str) -> dict[str, object]:
    while True:
        message = await socket.recv()
        if isinstance(message, bytes):
            continue
        frame = json.loads(message)
        if frame.get("type") == frame_type:
            return frame.get("body") or {}
        if frame.get("type") == "error":
            raise Stop(str((frame.get("body") or {}).get("message") or "refused"))


class _Link:
    """One socket's worth of the agent."""

    def __init__(self, agent: MachineAgent, socket) -> None:
        self._agent = agent
        self._socket = socket
        self._send_lock = asyncio.Lock()
        self._mux = mux.Mux(self._send_bytes, role="acceptor", accept=self._accept)

    async def _send_bytes(self, frame: bytes) -> None:
        async with self._send_lock:
            await self._socket.send(frame)

    async def _send_frame(self, frame_type: str, body: dict[str, object]) -> None:
        text = json.dumps(
            {"type": frame_type, "id": self._agent._frame_id(), "body": body}
        )
        async with self._send_lock:
            await self._socket.send(text)

    async def serve(self) -> tuple[int | None, str, int | None]:
        agent = self._agent
        await self._send_frame(
            "hello",
            {
                "role": "compute",
                "hello": agent._hello(),
                "machine": await agent.report(),
            },
        )
        welcome = await _expect(self._socket, "welcome")
        runtime = welcome.get("runtime") or {}
        heartbeat = int(welcome.get("heartbeat_ms") or 20_000) / 1000
        token = str(runtime.get("token") or "")
        logger.info("connected to Lemma as %s", welcome.get("name"))
        starting = asyncio.ensure_future(self._start_runtime(token))
        beating = asyncio.ensure_future(self._heartbeat(heartbeat))
        reconnect_after: int | None = None
        try:
            async for message in self._socket:
                if isinstance(message, bytes):
                    await self._mux.feed(message)
                    continue
                frame = json.loads(message)
                if frame.get("type") == "reconnect":
                    reconnect_after = int(
                        (frame.get("body") or {}).get("after_ms") or 0
                    )
                elif frame.get("type") == "error":
                    logger.warning("Lemma reported: %s", frame.get("body"))
        except ConnectionClosed:
            pass
        except mux.MuxError as error:
            logger.warning("a mux frame broke the protocol: %s", error)
        finally:
            for task in (starting, beating):
                task.cancel()
            await asyncio.gather(starting, beating, return_exceptions=True)
            await self._mux.close()
        return self._socket.close_code, self._socket.close_reason or "", reconnect_after

    async def _start_runtime(self, token: str) -> None:
        try:
            await self._agent.runtime.ensure(token)
        except RuntimeError as error:
            logger.error("the runtime did not start: %s", error)
            return
        await self._beat()

    async def _heartbeat(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._beat()

    async def _beat(self) -> None:
        await self._send_frame("control", {"machine": await self._agent.report()})

    # -------------------------------------------------------------- streams

    async def _accept(self, stream: mux.MuxStream, target: dict[str, object]) -> None:
        kind = target.get("kind")
        if kind == "port":
            await self._port(stream, target.get("port"))
        elif kind == "runtime_bundle":
            await self._runtime_bundle(stream, target)
        else:
            raise mux.StreamReset(mux.ResetCode.REFUSED, f"no target {kind!r} here")

    async def _port(self, stream: mux.MuxStream, port: object) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise mux.StreamReset(mux.ResetCode.REFUSED, "not a port")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            raise mux.StreamReset(
                mux.ResetCode.REFUSED, f"nothing is listening on port {port}"
            ) from None
        await self._mux.accept(stream)
        await mux.splice(stream, reader, writer)

    async def _runtime_bundle(
        self, stream: mux.MuxStream, target: dict[str, object]
    ) -> None:
        size = target.get("size")
        if not isinstance(size, int) or size > MAX_BUNDLE_BYTES:
            raise mux.StreamReset(mux.ResetCode.REFUSED, "a runtime too large")
        await self._mux.accept(stream)
        state = self._agent.config.state_directory
        with tempfile.NamedTemporaryFile(
            dir=state, prefix="runtime-", suffix=".zip", delete=False
        ) as handle:
            archive = Path(handle.name)
            received = 0
            while chunk := await stream.read():
                received += len(chunk)
                if received > size:
                    raise mux.StreamReset(mux.ResetCode.REFUSED, "more than announced")
                handle.write(chunk)
        try:
            installer = await self._agent.fetch("/agent-host/compute/runtime/installer")
            outcome = await self._agent.runtime.install(
                archive=archive,
                version=str(target.get("version")),
                archive_sha256=str(target.get("sha256")),
                requires=[str(name) for name in target.get("requires") or []],
                installer=installer,
            )
        except (InstallFailed, httpx.HTTPError, RuntimeError) as error:
            logger.error("installing the pushed runtime failed: %s", error)
            outcome = {"installed": False, "error": str(error)[:500]}
        finally:
            archive.unlink(missing_ok=True)
        await stream.write(json.dumps(outcome).encode())
        await stream.write_eof()
        await self._beat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A Lemma connected machine's agent")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    agent = MachineAgent(MachineConfig.read(args.config))

    async def run() -> int:
        if args.revoke:
            await agent.revoke()
            return 0
        loop = asyncio.get_running_loop()
        stopping = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stopping.set)
        running = asyncio.ensure_future(agent.run())
        waiting = asyncio.ensure_future(stopping.wait())
        try:
            await asyncio.wait({running, waiting}, return_when=asyncio.FIRST_COMPLETED)
            if running.done():
                running.result()
            return 0
        except Stop as stop:
            # Exit 0: stopping on purpose is not a crash, and a service
            # manager restarting a revoked machine would only be refused again.
            logger.warning("%s", stop)
            return 0
        finally:
            running.cancel()
            waiting.cancel()
            await asyncio.gather(running, waiting, return_exceptions=True)
            await agent.runtime.stop()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
