from __future__ import annotations

import argparse
import asyncio
import base64
from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import traceback
from uuid import UUID

from agentbox.function_runtime.process_protocol import (
    CancelRequest,
    ProcessManifest,
    ProcessStateRecord,
    SupervisorState,
)
from agentbox.observability import create_inherited_task


_ROOT = Path("/tmp/.agentbox/processes")
_MANIFEST_ENV = "AGENTBOX_PROCESS_MANIFEST"


class ProcessSupervisor:
    def __init__(self, manifest: ProcessManifest) -> None:
        self._manifest = manifest
        self._directory = _ROOT / str(manifest.operation_id)
        self._output_directory = self._directory / "output"
        self._input_directory = self._directory / "input"
        self._consumed_input_directory = self._directory / "consumed-input"
        self._control_directory = self._directory / "control"
        self._sequence = 0
        self._truncated_before: int | None = None
        self._retained_bytes = 0
        self._retained: deque[tuple[int, Path, int]] = deque()
        self._output_lock = asyncio.Lock()
        self._child: asyncio.subprocess.Process | None = None

    async def run(self) -> int:
        self._output_directory.mkdir(parents=True, exist_ok=True)
        self._input_directory.mkdir(parents=True, exist_ok=True)
        self._consumed_input_directory.mkdir(parents=True, exist_ok=True)
        self._control_directory.mkdir(parents=True, exist_ok=True)
        await self._write_state(SupervisorState.STARTING)
        argv = (
            ("/bin/bash", "-lc", self._manifest.shell_command)
            if self._manifest.shell_command is not None
            else self._manifest.argv
        )
        assert argv is not None
        environment = os.environ.copy()
        environment.update(
            {item.name: item.value for item in self._manifest.environment}
        )
        try:
            self._child = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._manifest.cwd,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except BaseException:
            await self._write_state(SupervisorState.FAILED, exit_code=127)
            self._remove_manifest()
            raise
        self._remove_manifest()
        await self._write_state(SupervisorState.RUNNING)

        stdout_task = create_inherited_task(self._drain(self._child.stdout, "stdout"))
        stderr_task = create_inherited_task(self._drain(self._child.stderr, "stderr"))
        input_task = create_inherited_task(self._forward_input())
        terminal_state = SupervisorState.FAILED
        try:
            terminal_state = await self._wait_for_terminal_request()
        finally:
            input_task.cancel()
            await asyncio.gather(input_task, return_exceptions=True)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        assert self._child.returncode is not None
        exit_code = self._child.returncode
        if terminal_state == SupervisorState.FAILED:
            terminal_state = (
                SupervisorState.SUCCEEDED if exit_code == 0 else SupervisorState.FAILED
            )
        await self._write_state(terminal_state, exit_code=exit_code)
        return 0

    async def _wait_for_terminal_request(self) -> SupervisorState:
        assert self._child is not None
        while self._child.returncode is None:
            cancellation = self._cancel_request()
            if cancellation is not None:
                await self._stop_child(cancellation.grace_seconds)
                return SupervisorState.CANCELLED
            if datetime.now(timezone.utc) >= self._manifest.deadline_at:
                await self._stop_child(0)
                return SupervisorState.TIMED_OUT
            try:
                await asyncio.wait_for(self._child.wait(), timeout=0.05)
            except TimeoutError:
                continue
        return SupervisorState.FAILED

    async def _stop_child(self, grace_seconds: float) -> None:
        assert self._child is not None
        if self._child.returncode is not None:
            return
        os.killpg(self._child.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self._child.wait(), timeout=grace_seconds)
        except TimeoutError:
            if self._child.returncode is None:
                os.killpg(self._child.pid, signal.SIGKILL)
                await self._child.wait()

    async def _drain(self, stream: asyncio.StreamReader | None, channel: str) -> None:
        if stream is None:
            return
        read_size = min(8192, self._manifest.output_limit_bytes)
        while data := await stream.read(read_size):
            await self._append_output(channel, data)

    async def _append_output(self, channel: str, data: bytes) -> None:
        async with self._output_lock:
            while (
                self._retained
                and self._retained_bytes + len(data) > self._manifest.output_limit_bytes
            ):
                sequence, path, size = self._retained.popleft()
                path.unlink(missing_ok=True)
                self._retained_bytes -= size
                self._truncated_before = sequence + 1
            sequence = self._sequence
            self._sequence += 1
            path = self._output_directory / f"{sequence:020d}-{channel}.bin"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
            self._retained.append((sequence, path, len(data)))
            self._retained_bytes += len(data)
            await self._write_state(SupervisorState.RUNNING)

    async def _forward_input(self) -> None:
        assert self._child is not None
        assert self._child.stdin is not None
        while self._child.returncode is None:
            paths = sorted(self._input_directory.glob("*.bin"))
            if not paths:
                await asyncio.sleep(0.02)
                continue
            for path in paths:
                try:
                    data = path.read_bytes()
                    self._child.stdin.write(data)
                    await self._child.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return
                finally:
                    if path.exists():
                        os.replace(path, self._consumed_input_directory / path.name)

    async def _write_state(
        self, state: SupervisorState, *, exit_code: int | None = None
    ) -> None:
        record = ProcessStateRecord(
            operation_id=self._manifest.operation_id,
            state=state,
            supervisor_pid=os.getpid(),
            child_process_group_id=(
                self._child.pid if self._child is not None else None
            ),
            next_sequence=self._sequence,
            truncated_before_sequence=self._truncated_before,
            exit_code=exit_code,
        )
        target = self._directory / "state.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        os.replace(temporary, target)

    def _cancel_request(self) -> CancelRequest | None:
        path = self._control_directory / "cancel.json"
        try:
            return CancelRequest.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return None

    def _remove_manifest(self) -> None:
        (self._directory / "request.json").unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation_id", type=UUID)
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    encoded = os.environ.pop(_MANIFEST_ENV, None)
    if encoded is not None:
        payload = base64.b64decode(encoded, validate=True)
    else:
        path = _ROOT / str(args.operation_id) / "request.json"
        payload = path.read_bytes()
    manifest = ProcessManifest.model_validate_json(payload)
    if manifest.operation_id != args.operation_id:
        raise ValueError("process manifest operation ID does not match command")
    return await ProcessSupervisor(manifest).run()


if __name__ == "__main__":
    try:
        result = asyncio.run(_main())
    except BaseException:
        try:
            operation_id = _parser().parse_args().operation_id
            error_path = Path(f"/tmp/agentbox-supervisor-{operation_id}.log")
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            raise
    raise SystemExit(result)
