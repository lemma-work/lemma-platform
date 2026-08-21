"""Operations inside a running E2B sandbox.

Split from lifecycle because the two answer different questions. Lifecycle asks
where a sandbox is and whether it should exist; this asks what is happening
inside one that already does. They share only the SDK handle.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime, timezone
import hashlib

import anyio

from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputSnapshot,
    ProcessState,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from app.core.request_context import create_inherited_task
from sandbox_runtime.errors import (
    SandboxPathNotFound,
    SandboxRejected,
)
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderGone,
    ProviderInstance,
)
from app.modules.workspace.providers.e2b_common import (
    sdk_best_effort,
    sdk_errors,
)
from app.modules.workspace.providers.e2b_process_lifetime import seconds_until
from app.modules.workspace.providers.e2b_python_runner import PYTHON_RUNNER

WORKSPACE_MOUNT = "/workspace"

# A process that has stopped will produce no further output, so a reader
# waiting for more has nothing left to wait for.
_FINISHED_PROCESS_STATES = frozenset(
    {
        ProcessState.SUCCEEDED,
        ProcessState.FAILED,
        ProcessState.CANCELLED,
        ProcessState.TIMED_OUT,
    }
)


def _has_finished(snapshot: ProcessOutputSnapshot) -> bool:
    return snapshot.state in _FINISHED_PROCESS_STATES


class E2BOpsMixin:
    """The `SandboxOpsProvider` half of the E2B provider.

    A mixin rather than a collaborator because the ops protocol is defined on
    the provider itself, and splitting it into an object the provider forwards
    to would add a layer that exists only to satisfy a line count.
    """

    async def _remember_pid(
        self, process_id: str, pid: int, *, tty: bool, sandbox_id: str = ""
    ) -> None:
        redis = self._redis()
        await redis.set(
            f"workspace:e2b:pid:v1:{process_id}", f"{pid}:{int(tty)}", ex=60 * 60
        )
        if sandbox_id:
            # A per-sandbox index, because "list the processes" means the ones
            # this platform started, not every pid inside the sandbox.
            key = f"workspace:e2b:procs:v1:{sandbox_id}"
            await redis.hset(key, process_id, f"{pid}:{int(tty)}")
            await redis.expire(key, 60 * 60)

    async def _recall_pid(self, process_id: str) -> tuple[int, bool]:
        raw = await self._redis().get(f"workspace:e2b:pid:v1:{process_id}")
        if raw is None:
            raise ProviderGone(f"process {process_id} is no longer tracked")
        return _decode_pid(raw)

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        """The processes this platform started in this sandbox.

        Read from our own index rather than E2B's process list, which reports
        every pid inside the sandbox and cannot say which operation any of them
        belongs to. State comes from the output buffer, which is where a
        process's completion is recorded.
        """
        entries = await self._redis().hgetall(
            f"workspace:e2b:procs:v1:{instance.provider_id}"
        )
        descriptors: list[ProcessDescriptor] = []
        for raw_id in entries:
            process_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            snapshot = await self._output.read(process_id, after_sequence=0)
            descriptors.append(
                ProcessDescriptor(
                    process_id=process_id,
                    state=snapshot.state,
                    exit_code=snapshot.exit_code,
                )
            )
        return tuple(descriptors)

    @staticmethod
    def _redis():
        from app.core.config import settings
        from app.core.infrastructure.redis.client import get_redis

        return get_redis(url=settings.redis_url)

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        sandbox = await self._connect(instance.provider_id)
        process_id = str(request.operation_id)
        await self._output.record_start(process_id)

        environment = {item.name: item.value for item in request.environment}
        command = request.shell_command or " ".join(request.argv or ())

        async def on_stdout(data: str) -> None:
            await self._output.append(
                process_id, channel=ProcessOutputChannel.STDOUT, data=data.encode()
            )

        async def on_stderr(data: str) -> None:
            await self._output.append(
                process_id, channel=ProcessOutputChannel.STDERR, data=data.encode()
            )

        process_seconds = seconds_until(deadline_at)

        with sdk_errors():
            if request.tty is not None:

                async def on_pty(data: bytes) -> None:
                    await self._output.append(
                        process_id, channel=ProcessOutputChannel.PTY, data=data
                    )

                handle = await sandbox.pty.create(
                    self._pty_size(rows=request.tty.rows, cols=request.tty.cols),
                    on_data=on_pty,
                    cwd=request.cwd,
                    envs=environment,
                    timeout=process_seconds,
                )
                if command:
                    # `pty.create` always starts a shell and takes no command,
                    # so the command is typed in. It must also be told to
                    # leave: otherwise the shell outlives the command and the
                    # process never reports completion, which is what a caller
                    # polls on. `exit $?` carries the command's status out.
                    await sandbox.pty.send_stdin(
                        handle.pid, f"{command}; exit $?\n".encode()
                    )
            else:
                handle = await sandbox.commands.run(
                    command,
                    background=True,
                    cwd=request.cwd,
                    envs=environment,
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                    timeout=process_seconds,
                )

        # The caller addresses the process by its operation id, so the E2B pid
        # is kept beside the output rather than handed upward.
        await self._remember_pid(
            process_id,
            handle.pid,
            tty=request.tty is not None,
            sandbox_id=instance.provider_id,
        )
        self._watch_for_exit(process_id, handle)
        return process_id

    def _watch_for_exit(self, process_id: str, handle) -> None:
        """Record the exit code when the process finishes.

        Nothing else can. E2B reports completion by resolving the handle, not
        by any state a later poll could read, so without this a finished
        command reads as still running forever: the caller sees no exit code,
        never treats it as complete, and polls until its deadline.
        """

        async def watch() -> None:
            try:
                outcome = await handle.wait()
                exit_code = getattr(outcome, "exit_code", None)
            except Exception as exc:
                # A command that exits non-zero raises in some SDK versions;
                # the exit code is still the thing the caller needs.
                exit_code = getattr(exc, "exit_code", None)
            except asyncio.CancelledError:
                # `wait()` awaits an SDK-internal task, so a cancellation
                # anywhere in that chain (a disconnect, a sandbox release)
                # arrives here — and `except Exception` does not catch it.
                # Skipping the record leaves the process reading as "still
                # running" for the rest of the sandbox's life, and the agent
                # polls a corpse until its own deadline. Record the outcome we
                # have, then let the cancellation continue.
                with anyio.CancelScope(shield=True):
                    await self._output.record_exit(process_id, exit_code=None)
                raise
            await self._output.record_exit(process_id, exit_code=exit_code)

        task = create_inherited_task(watch(), name=f"e2b-process-watch:{process_id}")
        # Held so the task is not garbage collected mid-flight, and discarded
        # once it has recorded the outcome.
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def read_process_output(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        snapshot = await self._output.read(process_id, after_sequence=after_sequence)
        if snapshot.chunks or wait_seconds <= 0 or _has_finished(snapshot):
            return snapshot

        # Nothing new yet. Poll the buffer rather than holding an E2B stream
        # open, so a caller that goes away costs nothing.
        #
        # Waking on the exit as well as on output is what makes this bounded by
        # the command instead of by the yield window. Exit is recorded by a
        # separate watcher task, so it almost always lands *after* the last
        # chunk: the collector's first poll took the output while the state was
        # still RUNNING, came back, saw a non-terminal state and polled again --
        # and this loop then had no new bytes to wait for and no reason to stop,
        # so it slept out the whole remaining window. Every command that printed
        # something and then exited paid that in full. Measured on a real
        # workspace, `lemma tables list` ran in 771 ms and the tool call around
        # it took 23-39 s, which agents read as a hang: they poll, give up, kill
        # the process and start again.
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
            snapshot = await self._output.read(
                process_id, after_sequence=after_sequence
            )
            if snapshot.chunks or _has_finished(snapshot):
                break
        return snapshot

    async def send_process_input(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        """Write to the process, on whichever channel it was started with.

        A PTY-backed process and a plain one are different objects in E2B with
        different input methods, so which one this is has to be remembered from
        the start; sending on the wrong one simply fails.
        """
        sandbox = await self._connect(instance.provider_id)
        pid, tty = await self._recall_pid(process_id)
        with sdk_errors():
            if tty:
                await sandbox.pty.send_stdin(pid, data)
            else:
                await sandbox.commands.send_stdin(pid, data)

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        pid, tty = await self._recall_pid(process_id)
        if not tty:
            # Resizing something with no terminal is a no-op, not a failure:
            # a caller should not have to know which it got.
            return
        with sdk_errors():
            await sandbox.pty.resize(
                pid, self._pty_size(rows=size.rows, cols=size.cols)
            )

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        pid, tty = await self._recall_pid(process_id)
        # A process that is already gone is the outcome asked for; the
        # state below is what the caller actually reads.
        with sdk_best_effort():
            await (sandbox.pty if tty else sandbox.commands).kill(pid)
        await self._output.record_cancelled(process_id)

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(path):
            info = await sandbox.files.get_info(path)
        return _to_stat(info)

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(path):
            entries = await sandbox.files.list(path)
        return tuple(_to_stat(entry) for entry in entries)

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            await sandbox.files.make_dir(path)

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(path):
            content = await sandbox.files.read(path, format="bytes")

        # E2B reads whole files, so the range is applied here. Callers use it
        # for previews and image thumbnails, where the file is small; a genuine
        # partial read of a huge file would need SDK support to avoid pulling
        # the whole thing.
        start = byte_range.offset or 0
        end = start + byte_range.length if byte_range.length else len(content)
        yield bytes(content)[start:end]

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        # Hashed as the chunks arrive rather than in one pass over the joined
        # payload. Both cost the same CPU; only this one has an await between
        # the blocks, so a large file is a series of short bursts instead of a
        # single uninterrupted one on the loop that also serves every other
        # agent run in this process. `filesystem_manager.write_stream` and
        # `stage_upload_limited` already do it this way.
        digest = hashlib.sha256() if expected_sha256 is not None else None
        blocks: list[bytes] = []
        async for chunk in data:
            blocks.append(chunk)
            if digest is not None:
                digest.update(chunk)
        payload = b"".join(blocks)
        if digest is not None and expected_sha256 is not None:
            hexdigest = digest.hexdigest()
            if hexdigest != expected_sha256.removeprefix("sha256:"):
                raise SandboxRejected(
                    f"content digest {hexdigest} does not match the expected value"
                )
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            info = await sandbox.files.write(path, payload)
        return _to_stat(info)

    async def move_file(
        self,
        instance: ProviderInstance,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(source):
            await sandbox.files.rename(source, destination)

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        sandbox = await self._connect(instance.provider_id)
        try:
            with sdk_errors(path):
                await sandbox.files.remove(path)
        except SandboxPathNotFound:
            # Deleting what is not there is the outcome asked for.
            return False
        return True

    # ------------------------------------------------------------------
    # Python sessions
    # ------------------------------------------------------------------

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        """No-op: a session is a file on disk, created on first execution.

        E2B has no resident-interpreter concept to reserve, so there is nothing
        to allocate ahead of time and pretending otherwise would mean tracking
        state with no backing.
        """
        return None

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        """Run code with REPL semantics and session continuity.

        The workspace image keeps a real interpreter per session. E2B's plain
        sandbox does not, so both properties are rebuilt here from what it does
        offer, and neither is faked:

        *Continuity* -- each execution restores the session's namespace from
        disk and saves it back, so a name bound in one call is available in the
        next. Only picklable values survive, which is the honest limit: an open
        file handle cannot cross a process boundary, and pretending it did
        would be worse than losing it.

        *A result* -- a REPL reports the value of a trailing expression, so the
        code is split with `ast` and the last node evaluated separately when it
        is an expression. Without this, `x = 6 * 7` followed by `x` returns
        nothing and an agent cannot see what it computed.
        """
        sandbox = await self._connect(instance.provider_id)
        state_path = f"/tmp/lemma-python-{session.session_id}.pkl"
        code_path = f"/tmp/lemma-python-{request.operation_id}.code"
        result_path = f"/tmp/lemma-python-{request.operation_id}.result"
        runner_path = f"/tmp/lemma-python-{request.operation_id}.py"

        with sdk_errors():
            await sandbox.files.write(code_path, request.code)
            await sandbox.files.write(
                runner_path,
                PYTHON_RUNNER.format(
                    state_path=state_path,
                    code_path=code_path,
                    result_path=result_path,
                ),
            )
            outcome = await sandbox.commands.run(
                f"python3 {runner_path}",
                envs={item.name: item.value for item in request.environment},
                # `None` here meant unbounded, so `execute_python`'s
                # `timeout_seconds` bounded only how long the backend waited --
                # nothing stopped the code itself. A runaway loop kept running
                # in the sandbox after the tool had returned, holding CPU and
                # memory on a box with one core, and the idle sweeper will not
                # release a sandbox with live processes.
                timeout=seconds_until(request.deadline_at),
            )

        # No trailing expression, or a run that failed before writing one,
        # both mean there is no result to report.
        result: str | None = None
        with sdk_best_effort(result_path):
            raw = await sandbox.files.read(result_path, format="text")
            result = raw if raw else None

        failed = outcome.exit_code != 0
        return PythonResult(
            operation_id=request.operation_id,
            state=(
                PythonExecutionState.FAILED
                if failed
                else PythonExecutionState.SUCCEEDED
            ),
            stdout=outcome.stdout or "",
            stderr=outcome.stderr or "",
            result=result,
            error_name="ExecutionError" if failed else None,
            error_message=(outcome.stderr or None) if failed else None,
            traceback=(),
            output_truncated=False,
        )

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        # Nothing to forget is success.
        with sdk_best_effort():
            await sandbox.files.remove(f"/tmp/lemma-python-{session_id}.pkl")

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    async def port_base_url(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> str:
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            return f"https://{sandbox.get_host(port)}"


def _decode_pid(raw) -> tuple[int, bool]:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    pid, _, flag = text.partition(":")
    return int(pid), flag == "1"


def _to_stat(entry) -> FileStat:
    is_dir = str(getattr(entry, "type", "")).lower().endswith("dir")
    modified = getattr(entry, "modified_time", None)
    return FileStat(
        path=entry.path,
        kind=FileKind.DIRECTORY if is_dir else FileKind.FILE,
        size_bytes=int(getattr(entry, "size", 0) or 0),
        modified_at=modified or datetime.now(timezone.utc),
        # E2B reports permissions as a string when it reports them at all;
        # 0o644/0o755 is the honest default rather than inventing a number.
        mode=int(getattr(entry, "mode", 0) or (0o755 if is_dir else 0o644)),
    )
