"""E2B sandbox provider.

Written against the E2B SDK as it is now, not as the previous adapter found it.
Three capabilities that adapter predated do most of the work here, and each one
removes machinery rather than adding it:

*Metadata queries* give E2B an identity mechanism. A sandbox is created with
`{lemma-sandbox-id, lemma-epoch}` in its metadata and found again by querying
for it. That is the same idea as the deterministic container name on Docker --
identity derived from durable state -- so create is idempotent here too, and
for the same reason: look before creating, and a retry after a lost response
finds the sandbox instead of making a second one.

*Pause and resume* is a real suspend primitive. Releasing a sandbox pauses it
and keeps its disk; the next ensure reconnects to the same sandbox. The
previous adapter had to destroy and recreate, which is why it needed a separate
notion of native storage that outlived the sandbox.

*Volumes* give durable storage a name we choose, so a workspace's disk is the
same kind of object on both providers and `find_volume` means the same thing.

What is deliberately *not* carried over: layered retry loops around every call.
A provider's job is to report what happened; deciding whether to wait and try
again belongs to the service, which is the only layer that knows the caller's
deadline. Errors here are classified and raised, not absorbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputSnapshot,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.domain.errors import (
    SandboxPathNotFound,
    SandboxRejected,
    SandboxUnavailable,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderFailed,
    ProviderGone,
    ProviderInstance,
    ProviderObject,
    ProviderRejected,
)
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

# E2B metadata values are strings, so identity travels as strings and is parsed
# back on the way in.
META_SANDBOX_ID = "lemma-sandbox-id"
META_SANDBOX_KIND = "lemma-sandbox-kind"
META_EPOCH = "lemma-epoch"

WORKSPACE_MOUNT = "/workspace"


@dataclass(frozen=True, slots=True)
class E2BProviderConfig:
    api_key: str
    workspace_template: str
    function_template: str
    # How long E2B keeps a sandbox alive without contact. The service touches
    # activity on use, so this is a backstop against leaking compute when the
    # backend dies, not the primary idle policy.
    sandbox_timeout_seconds: int = 60 * 30
    domain: str | None = None


class E2BSandboxProvider:
    name = "e2b"

    def __init__(
        self, config: E2BProviderConfig, *, output: E2BOutputBuffer | None = None
    ) -> None:
        self._config = config
        self._output = output or E2BOutputBuffer()

    # ------------------------------------------------------------------
    # SDK access, imported lazily so a Docker-only deployment never loads it
    # ------------------------------------------------------------------

    @property
    def _sdk(self):
        try:
            from e2b import AsyncSandbox
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise ProviderRejected(
                "the e2b extra is not installed; install lemma-backend[e2b]"
            ) from exc
        return AsyncSandbox

    @property
    def _volumes(self):
        from e2b.volume.volume_async import AsyncVolume

        return AsyncVolume

    def _api(self) -> dict[str, object]:
        params: dict[str, object] = {"api_key": self._config.api_key}
        if self._config.domain:
            params["domain"] = self._config.domain
        return params

    def _template(self, kind: SandboxKind) -> str:
        return (
            self._config.function_template
            if kind is SandboxKind.FUNCTION
            else self._config.workspace_template
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        # Idempotence, the E2B way: identity lives in metadata rather than in a
        # name, so the lookup is a query instead of an inspect. The property
        # that matters is the same -- a retry finds what a lost response left
        # behind rather than creating a second sandbox.
        existing = await self._find(spec.sandbox_id, epoch=spec.epoch)
        if existing is not None:
            return existing

        volume_mounts = None
        if spec.volume_name:
            volume = await self._connect_volume(spec.volume_name)
            volume_mounts = {WORKSPACE_MOUNT: volume}

        try:
            sandbox = await self._sdk.create(
                template=self._template(spec.kind),
                timeout=self._config.sandbox_timeout_seconds,
                metadata={
                    META_SANDBOX_ID: str(spec.sandbox_id),
                    META_SANDBOX_KIND: spec.kind.value,
                    META_EPOCH: str(spec.epoch),
                },
                envs=dict(spec.env),
                volume_mounts=volume_mounts,
                **self._api(),
            )
        except Exception as exc:
            raise _classify(exc) from exc

        return ProviderInstance(
            provider_id=sandbox.sandbox_id,
            name=spec.name,
            volume_name=spec.volume_name,
            running=True,
        )

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        """Look a sandbox up by the name the service assigned it.

        The service's names carry the identity, so they are parsed back into a
        metadata query rather than being sent to E2B, which knows nothing about
        them.
        """
        from app.modules.workspace.providers import naming

        parsed = naming.parse_container_name(name)
        if parsed is None:
            return None
        sandbox_id, _, epoch = parsed
        return await self._find(sandbox_id, epoch=epoch)

    async def wait_ready(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """E2B returns a sandbox that is already serving.

        `create` does not resolve until the sandbox accepts commands, and a
        resumed sandbox is reachable as soon as `connect` returns, so there is
        no readiness loop to run. A paused sandbox is resumed here rather than
        being reported not-ready, because resuming is what the caller wants.
        """
        sandbox = await self._connect(instance.provider_id)
        try:
            if not await sandbox.is_running(**self._api()):
                raise ProviderFailed(
                    f"e2b sandbox {instance.provider_id} is not running"
                )
        except ProviderFailed:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def release(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """Pause, keeping the filesystem. The next ensure resumes this sandbox."""
        sandbox = await self._connect(instance.provider_id)
        try:
            await sandbox.beta_pause(**self._api())
        except Exception as exc:
            raise _classify(exc) from exc

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        instance = await self.inspect(name, deadline_at=deadline_at)
        if instance is None:
            # Already gone is the outcome destroy was asking for.
            return
        sandbox = await self._connect(instance.provider_id)
        try:
            await sandbox.kill(**self._api())
        except Exception as exc:
            raise _classify(exc) from exc

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        from app.modules.workspace.providers import naming

        try:
            volumes = await self._volumes.list(**self._api())
        except Exception as exc:
            raise _classify(exc) from exc

        # Volume names carry the generation, so the newest one wins if an
        # earlier disk was replaced and its volume is still lying around.
        owned = sorted(
            (
                volume.name
                for volume in volumes
                if volume.name
                and volume.name.startswith(f"lemma-vol-{sandbox_id.hex}-")
            ),
            key=lambda candidate: _generation_of(candidate),
            reverse=True,
        )
        del naming
        return owned[0] if owned else None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str:
        existing = await self.find_volume(
            sandbox_id=sandbox_id, deadline_at=deadline_at
        )
        if existing == name:
            return existing
        try:
            volume = await self._volumes.create(name, **self._api())
        except Exception as exc:
            raise _classify(exc) from exc
        return volume.name or name

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        try:
            volume = await self._connect_volume(name)
            await self._volumes.destroy(volume.volume_id, **self._api())
        except ProviderGone:
            return
        except Exception as exc:
            raise _classify(exc) from exc

    async def _connect_volume(self, name: str):
        try:
            volumes = await self._volumes.list(**self._api())
        except Exception as exc:
            raise _classify(exc) from exc
        for volume in volumes:
            if volume.name == name:
                return await self._volumes.connect(volume.volume_id, **self._api())
        raise ProviderGone(f"e2b volume {name} does not exist")

    # ------------------------------------------------------------------
    # Reclamation
    # ------------------------------------------------------------------

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """Every sandbox carrying this platform's metadata.

        Sandboxes without it belong to something else using the same E2B
        account and are not ours to reap.
        """
        from e2b.sandbox.sandbox_api import SandboxQuery

        found: list[ProviderObject] = []
        try:
            paginator = self._sdk.list(
                query=SandboxQuery(metadata={META_SANDBOX_KIND: "workspace"}),
                **self._api(),
            )
            pages = await paginator.next_items()
        except Exception as exc:
            raise _classify(exc) from exc

        for info in pages:
            metadata = info.metadata or {}
            raw_id = metadata.get(META_SANDBOX_ID)
            if not raw_id:
                continue
            try:
                sandbox_id = UUID(raw_id)
            except ValueError:
                continue
            epoch = None
            raw_epoch = metadata.get(META_EPOCH)
            if raw_epoch:
                try:
                    epoch = int(raw_epoch)
                except ValueError:
                    epoch = None
            found.append(
                ProviderObject(
                    provider_id=info.sandbox_id,
                    name=info.sandbox_id,
                    sandbox_id=sandbox_id,
                    epoch=epoch,
                    running=str(info.state).lower().endswith("running"),
                    legacy=False,
                )
            )
        return tuple(found)

    async def close(self) -> None:
        return None

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

        try:
            if request.tty is not None:
                async def on_pty(data: bytes) -> None:
                    await self._output.append(
                        process_id, channel=ProcessOutputChannel.PTY, data=data
                    )

                from e2b.sandbox.commands.command_handle import PtySize

                handle = await sandbox.pty.create(
                    PtySize(rows=request.tty.rows, cols=request.tty.cols),
                    on_data=on_pty,
                    cwd=request.cwd,
                    envs=environment,
                )
                if command:
                    await sandbox.pty.send_stdin(
                        handle.pid, (command + "\n").encode()
                    )
            else:
                handle = await sandbox.commands.run(
                    command,
                    background=True,
                    cwd=request.cwd,
                    envs=environment,
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                )
        except Exception as exc:
            raise _classify(exc) from exc

        # The caller addresses the process by its operation id, so the E2B pid
        # is kept beside the output rather than handed upward.
        await self._remember_pid(process_id, handle.pid)
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
        import asyncio

        snapshot = await self._output.read(process_id, after_sequence=after_sequence)
        if snapshot.chunks or wait_seconds <= 0:
            return snapshot

        # Nothing new yet. Poll the buffer rather than holding an E2B stream
        # open, so a caller that goes away costs nothing.
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
            snapshot = await self._output.read(
                process_id, after_sequence=after_sequence
            )
            if snapshot.chunks:
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
        sandbox = await self._connect(instance.provider_id)
        pid = await self._recall_pid(process_id)
        try:
            await sandbox.commands.send_stdin(pid, data)
        except Exception as exc:
            raise _classify(exc) from exc

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        from e2b.sandbox.commands.command_handle import PtySize

        sandbox = await self._connect(instance.provider_id)
        pid = await self._recall_pid(process_id)
        try:
            await sandbox.pty.resize(pid, PtySize(rows=size.rows, cols=size.cols))
        except Exception as exc:
            raise _classify(exc) from exc

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        pid = await self._recall_pid(process_id)
        try:
            await sandbox.commands.kill(pid)
        except Exception:
            # A process that is already gone is the outcome asked for; the
            # state below is what the caller actually reads.
            pass
        await self._output.record_cancelled(process_id)

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        sandbox = await self._connect(instance.provider_id)
        try:
            info = await sandbox.files.get_info(path)
        except Exception as exc:
            raise _classify_path(exc, path) from exc
        return _to_stat(info)

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        sandbox = await self._connect(instance.provider_id)
        try:
            entries = await sandbox.files.list(path)
        except Exception as exc:
            raise _classify_path(exc, path) from exc
        return tuple(_to_stat(entry) for entry in entries)

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        try:
            await sandbox.files.make_dir(path)
        except Exception as exc:
            raise _classify(exc) from exc

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        sandbox = await self._connect(instance.provider_id)
        try:
            content = await sandbox.files.read(path, format="bytes")
        except Exception as exc:
            raise _classify_path(exc, path) from exc

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
        payload = b"".join([chunk async for chunk in data])
        if expected_sha256 is not None:
            import hashlib

            digest = hashlib.sha256(payload).hexdigest()
            if digest != expected_sha256.removeprefix("sha256:"):
                raise SandboxRejected(
                    f"content digest {digest} does not match the expected value"
                )
        sandbox = await self._connect(instance.provider_id)
        try:
            info = await sandbox.files.write(path, payload)
        except Exception as exc:
            raise _classify(exc) from exc
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
        try:
            await sandbox.files.rename(source, destination)
        except Exception as exc:
            raise _classify_path(exc, source) from exc

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
            await sandbox.files.remove(path)
        except Exception as exc:
            classified = _classify_path(exc, path)
            if isinstance(classified, SandboxPathNotFound):
                # Deleting what is not there is the outcome asked for.
                return False
            raise classified from exc
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
        """Run code with the session's variables carried across executions.

        The workspace image keeps a real interpreter per session. E2B's plain
        sandbox does not, so continuity is provided the only way it honestly
        can be here: each execution runs in a fresh interpreter that restores
        and re-saves the session's namespace around the user's code.
        """
        sandbox = await self._connect(instance.provider_id)
        state_path = f"/tmp/lemma-python-{session.session_id}.pkl"
        program = _SESSION_PREAMBLE.format(state_path=state_path) + request.code
        script_path = f"/tmp/lemma-python-{request.operation_id}.py"

        try:
            await sandbox.files.write(script_path, program)
            result = await sandbox.commands.run(
                f"python3 {script_path}",
                envs={item.name: item.value for item in request.environment},
                timeout=None,
            )
        except Exception as exc:
            raise _classify(exc) from exc

        failed = result.exit_code != 0
        return PythonResult(
            operation_id=request.operation_id,
            state=(
                PythonExecutionState.FAILED
                if failed
                else PythonExecutionState.SUCCEEDED
            ),
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            result=None,
            error_name="ExecutionError" if failed else None,
            error_message=(result.stderr or None) if failed else None,
            traceback=(),
            output_truncated=False,
        )

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        sandbox = await self._connect(instance.provider_id)
        try:
            await sandbox.files.remove(f"/tmp/lemma-python-{session_id}.pkl")
        except Exception:
            # Nothing to forget is success.
            return

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    async def port_base_url(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> str:
        sandbox = await self._connect(instance.provider_id)
        try:
            return f"https://{sandbox.get_host(port)}"
        except Exception as exc:
            raise _classify(exc) from exc

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find(self, sandbox_id: UUID, *, epoch: int) -> ProviderInstance | None:
        from e2b.sandbox.sandbox_api import SandboxQuery

        try:
            paginator = self._sdk.list(
                query=SandboxQuery(
                    metadata={
                        META_SANDBOX_ID: str(sandbox_id),
                        META_EPOCH: str(epoch),
                    }
                ),
                **self._api(),
            )
            matches = await paginator.next_items()
        except Exception as exc:
            raise _classify(exc) from exc

        for info in matches:
            return ProviderInstance(
                provider_id=info.sandbox_id,
                name=info.sandbox_id,
                volume_name=None,
                running=str(info.state).lower().endswith("running"),
            )
        return None

    async def _connect(self, provider_id: str):
        try:
            return await self._sdk.connect(provider_id, **self._api())
        except Exception as exc:
            classified = _classify(exc)
            if isinstance(classified, ProviderGone):
                raise classified from exc
            raise classified from exc

    async def _remember_pid(self, process_id: str, pid: int) -> None:
        from app.core.infrastructure.redis.client import get_redis
        from app.core.config import settings

        await get_redis(url=settings.redis_url).set(
            f"workspace:e2b:pid:v1:{process_id}", str(pid), ex=60 * 60
        )

    async def _recall_pid(self, process_id: str) -> int:
        from app.core.infrastructure.redis.client import get_redis
        from app.core.config import settings

        raw = await get_redis(url=settings.redis_url).get(
            f"workspace:e2b:pid:v1:{process_id}"
        )
        if raw is None:
            raise ProviderGone(f"process {process_id} is no longer tracked")
        return int(raw)


_SESSION_PREAMBLE = '''
import pickle as _p, os as _o
_S = "{state_path}"
if _o.path.exists(_S):
    try:
        globals().update(_p.load(open(_S, "rb")))
    except Exception:
        pass
import atexit as _a
def _save():
    _keep = {{}}
    for _k, _v in list(globals().items()):
        if _k.startswith("_"):
            continue
        try:
            _p.dumps(_v)
        except Exception:
            continue
        _keep[_k] = _v
    try:
        _p.dump(_keep, open(_S, "wb"))
    except Exception:
        pass
_a.register(_save)
'''


def _generation_of(volume_name: str) -> int:
    tail = volume_name.rsplit("-", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


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


def _classify(exc: Exception) -> Exception:
    """Turn an SDK failure into this module's two-axis vocabulary.

    Deliberately no retry loop. Whether waiting could help is a question about
    the caller's deadline, which the service owns; the provider's job is to say
    what happened.
    """
    name = type(exc).__name__
    message = str(exc)

    if "NotFound" in name or "not found" in message.lower():
        return ProviderGone(message)
    if "RateLimit" in name or "429" in message:
        return SandboxUnavailable(message, retry_after_ms=2000)
    if "Timeout" in name or "timeout" in message.lower():
        return SandboxUnavailable(message, retry_after_ms=1000)
    if "Authentication" in name or "401" in message or "403" in message:
        return ProviderRejected(f"e2b rejected the credentials: {message}")
    if "Invalid" in name or "400" in message:
        return ProviderRejected(message)
    # Unknown failures are treated as worth retrying: E2B is a network service,
    # and a permanent failure will simply fail again with the same message.
    return SandboxUnavailable(message, retry_after_ms=1000)


def _classify_path(exc: Exception, path: str) -> Exception:
    name = type(exc).__name__
    if "NotFound" in name or "not found" in str(exc).lower():
        return SandboxPathNotFound(f"{path} does not exist")
    return _classify(exc)
