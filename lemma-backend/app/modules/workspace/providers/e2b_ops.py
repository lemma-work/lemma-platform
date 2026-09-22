"""Operations inside a running E2B sandbox.

Split from lifecycle because the two answer different questions. Lifecycle asks
where a sandbox is and whether it should exist; this asks what is happening
inside one that already does. They share only the SDK handle.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
import hashlib


from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputSnapshot,
    ProcessState,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from sandbox_runtime.errors import (
    SandboxPathConflict,
    SandboxPathNotFound,
)
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    PythonResult,
    ProviderCapability,
    ProviderGone,
    ProviderInstance,
)
from app.modules.workspace.providers.e2b_common import (
    sdk_best_effort,
    sdk_errors,
)
from app.modules.workspace.providers.e2b_paths import resolve_real_path
from app.modules.workspace.providers.e2b_ranged_read import read_range
from app.modules.workspace.providers.e2b_reach import E2BReachMixin
from app.modules.workspace.providers.e2b_process_index import (
    ENTRY_TTL_SECONDS,
    decode_pid,
    encode_entry,
    index_key,
    pid_key,
    read_descriptors,
)
from app.modules.workspace.providers.e2b_process_lifetime import (
    seconds_until,
    watch_for_exit,
)
from app.modules.workspace.providers import e2b_python_sessions

from app.core.log.log import get_logger

logger = get_logger(__name__)

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


class E2BOpsMixin(E2BReachMixin):
    """The `SandboxOpsProvider` half of the E2B provider.

    A mixin rather than a collaborator because the ops protocol is defined on
    the provider itself, and splitting it into an object the provider forwards
    to would add a layer that exists only to satisfy a line count.
    """

    capabilities = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )

    async def _remember_pid(
        self,
        process_id: str,
        pid: int,
        *,
        tty: bool,
        sandbox_id: str = "",
        expires_at: float = 0.0,
        cwd: str = "",
        command: str = "",
    ) -> None:
        redis = self._redis()
        entry = encode_entry(
            pid,
            tty=tty,
            expires_at=expires_at,
            cwd=cwd,
            command=command,
            started_at=datetime.now(timezone.utc).timestamp(),
        )
        await redis.set(pid_key(process_id), entry, ex=ENTRY_TTL_SECONDS)
        if sandbox_id:
            key = index_key(sandbox_id)
            await redis.hset(key, process_id, entry)
            await redis.expire(key, ENTRY_TTL_SECONDS)

    async def _recall_pid(self, process_id: str) -> tuple[int, bool]:
        raw = await self._redis().get(pid_key(process_id))
        if raw is None:
            raise ProviderGone(f"process {process_id} is no longer tracked")
        return decode_pid(raw)

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        """The processes this platform started in this sandbox.

        Read from our own index rather than E2B's process list, which reports
        every pid inside the sandbox and cannot say which operation any of them
        belongs to. State comes from the output buffer, which is where a
        process's completion is recorded, corrected by the recorded deadline --
        see `e2b_process_index.read_descriptors`.
        """
        del deadline_at
        return await read_descriptors(
            self._redis(),
            self._output,
            sandbox_id=instance.provider_id,
            now=datetime.now(timezone.utc).timestamp(),
        )

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
            expires_at=deadline_at.timestamp(),
            cwd=request.cwd or "",
            command=command,
        )
        watch_for_exit(self._output, self._watchers, process_id, handle)
        return process_id

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
        """Where this path really is, and whether it is a link.

        E2B's file SDK answers neither, and the files API's containment check
        needs both -- see `e2b_paths.resolve_real_path`, which is where that is
        explained and where the boundary is enforced.
        """
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(path):
            info = await sandbox.files.get_info(path)
        stat = _to_stat(info)
        resolved, is_link = await resolve_real_path(sandbox, path)
        return replace(
            stat,
            path=resolved,
            kind=FileKind.SYMLINK if is_link else stat.kind,
        )

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
        # Not `files.read`: it has no notion of a range and returns the whole
        # file for the caller to slice, which made a 1 GiB download into 128
        # requests of 1 GiB each. See `e2b_ranged_read`.
        async for chunk in read_range(
            sandbox, path=path, byte_range=byte_range, deadline_at=deadline_at
        ):
            yield chunk

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        sandbox = await self._connect(instance.provider_id)
        if expected_sha256 is not None:
            # A precondition on what is already at the path, which is what the
            # contract says and what the runtime-backed fabrics have always
            # done. This used to hash the *outgoing* bytes instead, so the same
            # argument meant opposite things: on E2B it asked "am I sending
            # what I think I am", and everywhere else "is the file I am about
            # to replace the one I read". Passing the digest therefore
            # satisfied E2B and made the very first install impossible on
            # Docker and Desktop, which is why the one caller that wants this
            # stopped passing it at all.
            await self._require_existing_digest(
                sandbox, path=path, expected_sha256=expected_sha256
            )
        blocks: list[bytes] = []
        async for chunk in data:
            blocks.append(chunk)
        payload = b"".join(blocks)
        with sdk_errors():
            info = await sandbox.files.write(path, payload)
        return _to_stat(info)

    async def _require_existing_digest(
        self, sandbox: object, *, path: str, expected_sha256: str
    ) -> None:
        """Refuse unless the file already at `path` hashes to this.

        A missing file fails the precondition rather than passing it: the
        caller is saying "replace the exact bytes I read", and there being
        nothing there means it is not replacing them.
        """
        wanted = expected_sha256.removeprefix("sha256:")
        try:
            # `sdk_errors(path)`, not the bare form: on a filesystem call "not
            # found" is a missing file, and the bare form classifies it as a
            # missing *sandbox*. `ProviderGone` is deliberately not caught
            # below -- a sandbox that is gone is not a content conflict, and
            # answering one with the other would tell a caller to give up
            # instead of re-ensuring.
            with sdk_errors(path):
                current = await sandbox.files.read(path, format="bytes")
        except SandboxPathNotFound as exc:
            raise SandboxPathConflict(
                f"{path} does not exist, so its content cannot match"
            ) from exc
        found = hashlib.sha256(bytes(current)).hexdigest()
        if found != wanted:
            raise SandboxPathConflict(f"{path} holds {found}, not the expected content")

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

    # Delegated rather than inherited: these need the sandbox connection and
    # output buffer above, which an argument carries as well as a base class
    # would, and the provider already has three.
    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        await e2b_python_sessions.ensure_python_session(self, instance, request)

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        return await e2b_python_sessions.execute_python(
            self, instance, session, request
        )

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        await e2b_python_sessions.delete_python_session(
            self, instance, session_id=session_id, deadline_at=deadline_at
        )


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
