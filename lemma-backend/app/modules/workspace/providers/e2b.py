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

*The sandbox is the disk.* Verified against the real service: write a file,
pause, reconnect, and it is still there. That is how production stores every
workspace today -- the account holds no volumes at all, and volumes are not a
public E2B feature. So `storage_kind` is SANDBOX_NATIVE, and adoption is not
an optimisation but the only safe behaviour: creating a second sandbox for an
existing identity would leave the user's files in the first with nothing
pointing at it. Adoption therefore ignores the epoch, and the fence becomes the
E2B sandbox id, which only changes when the sandbox genuinely is new.

What is deliberately *not* carried over: layered retry loops around every call.
A provider's job is to report what happened; deciding whether to wait and try
again belongs to the service, which is the only layer that knows the caller's
deadline. Errors here are classified and raised, not absorbed.
"""

from __future__ import annotations

import asyncio
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

from app.core.request_context import create_inherited_task
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
    ProviderStorageKind,
    ProcessDescriptor,
)
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

# E2B metadata values are strings, so identity travels as strings and is parsed
# back on the way in.
#
# The prefix is configurable rather than hardcoded because one E2B account is
# shared: a conformance run must be able to label its sandboxes so that neither
# its queries nor its sweeps can ever see production's, and vice versa.
DEFAULT_METADATA_NAMESPACE = "lemma"


def meta_sandbox_id(namespace: str) -> str:
    return f"{namespace}-sandbox-id"


def meta_sandbox_kind(namespace: str) -> str:
    return f"{namespace}-sandbox-kind"


def meta_epoch(namespace: str) -> str:
    return f"{namespace}-epoch"


# The production namespace, kept as module constants for readability at the
# call sites that do not vary.
META_SANDBOX_ID = meta_sandbox_id(DEFAULT_METADATA_NAMESPACE)
META_SANDBOX_KIND = meta_sandbox_kind(DEFAULT_METADATA_NAMESPACE)
META_EPOCH = meta_epoch(DEFAULT_METADATA_NAMESPACE)
# What AgentBox stamped on the same sandboxes. Read-only: matched so existing
# production workspaces are adopted, never written.
LEGACY_MANAGED_BY_KEY = "managed-by"
LEGACY_MANAGED_BY = "agentbox"
LEGACY_LOGICAL_ID = "logical-id"

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
    # Namespaces every metadata key this provider writes and queries. Changing
    # it makes a provider blind to sandboxes labelled by another namespace,
    # which is exactly what a conformance run against a shared account needs.
    metadata_namespace: str = DEFAULT_METADATA_NAMESPACE
    # Whether pre-consolidation AgentBox sandboxes may be adopted. Off for any
    # namespace but the production one: a test must never adopt a real user's
    # workspace.
    adopt_legacy: bool = True


class E2BSandboxProvider:
    name = "e2b"
    # A paused E2B sandbox keeps its filesystem, so the sandbox *is* the disk.
    # Verified against the real service: write, pause, reconnect, read back.
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE

    def __init__(
        self, config: E2BProviderConfig, *, output: E2BOutputBuffer | None = None
    ) -> None:
        self._config = config
        self._output = output or E2BOutputBuffer()
        self._watchers: set[asyncio.Task[None]] = set()

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

    def _identity_metadata(self, spec: ProviderCreateSpec) -> dict[str, str]:
        namespace = self._config.metadata_namespace
        return {
            meta_sandbox_id(namespace): str(spec.sandbox_id),
            meta_sandbox_kind(namespace): spec.kind.value,
            meta_epoch(namespace): str(spec.epoch),
        }

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
        """Adopt this sandbox's existing E2B sandbox, or make its first one.

        Because the sandbox is also the disk, "create" here means "make sure
        the one sandbox that holds this workspace's files exists and is
        running". Creating a second one would leave the user's files stranded
        in the first, which is the whole reason adoption is not optional --
        production workspaces today are paused sandboxes with no volume behind
        them.

        Adoption deliberately ignores the epoch. On Docker the epoch fences a
        container that can be replaced independently of its volume; here
        replacement would destroy the disk, so identity alone decides, and the
        fence is the E2B sandbox id -- a genuinely new sandbox has a new id, so
        a stale operation fails rather than landing on it.
        """
        existing = await self._find_any(spec.sandbox_id)
        if existing is not None:
            await self._stamp(existing.provider_id, spec)
            return ProviderInstance(
                provider_id=existing.provider_id,
                name=spec.name,
                running=existing.running,
                storage_adopted=True,
            )

        try:
            sandbox = await self._sdk.create(
                template=self._template(spec.kind),
                timeout=self._config.sandbox_timeout_seconds,
                metadata=self._identity_metadata(spec),
                envs=dict(spec.env),
                **self._api(),
            )
        except Exception as exc:
            raise _classify(exc) from exc

        return ProviderInstance(
            provider_id=sandbox.sandbox_id,
            name=spec.name,
            running=True,
            storage_adopted=False,
        )

    async def _stamp(self, provider_id: str, spec: ProviderCreateSpec) -> None:
        """Best effort: record the current epoch on an adopted sandbox.

        Only bookkeeping -- identity and the fence come from the sandbox id, so
        an SDK without metadata updates costs nothing but a stale epoch label.
        """
        try:
            sandbox = await self._connect(provider_id)
            setter = getattr(sandbox, "set_metadata", None)
            if setter is not None:
                await setter(self._identity_metadata(spec), **self._api())
        except Exception:
            return

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
        sandbox_id, _, _ = parsed
        # Epoch is not part of the lookup: the sandbox is adopted across
        # epochs because destroying it would destroy the user's files.
        return await self._find_any(sandbox_id)

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
            if not await sandbox.is_running():
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
        """Always None: E2B storage is the sandbox, not a separate volume.

        The service skips this entirely for SANDBOX_NATIVE providers. It is
        implemented so the protocol is satisfied and so a future caller that
        forgets the distinction gets "no volume" rather than a crash.
        """
        return None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str:
        raise ProviderRejected(
            "e2b storage lives in the sandbox itself; there is no volume to create"
        )

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        """Nothing to destroy separately: killing the sandbox takes the disk."""
        return None

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
                query=SandboxQuery(
                    metadata={
                        meta_sandbox_kind(
                            self._config.metadata_namespace
                        ): SandboxKind.WORKSPACE.value
                    }
                ),
                **self._api(),
            )
            pages = await paginator.next_items()
        except Exception as exc:
            raise _classify(exc) from exc

        for info in pages:
            metadata = info.metadata or {}
            raw_id = metadata.get(meta_sandbox_id(self._config.metadata_namespace))
            if not raw_id:
                continue
            try:
                sandbox_id = UUID(raw_id)
            except ValueError:
                continue
            epoch = None
            raw_epoch = metadata.get(meta_epoch(self._config.metadata_namespace))
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
                )
        except Exception as exc:
            raise _classify(exc) from exc

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
        """Write to the process, on whichever channel it was started with.

        A PTY-backed process and a plain one are different objects in E2B with
        different input methods, so which one this is has to be remembered from
        the start; sending on the wrong one simply fails.
        """
        sandbox = await self._connect(instance.provider_id)
        pid, tty = await self._recall_pid(process_id)
        try:
            if tty:
                await sandbox.pty.send_stdin(pid, data)
            else:
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
        pid, tty = await self._recall_pid(process_id)
        if not tty:
            # Resizing something with no terminal is a no-op, not a failure:
            # a caller should not have to know which it got.
            return
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
        pid, tty = await self._recall_pid(process_id)
        try:
            await (sandbox.pty if tty else sandbox.commands).kill(pid)
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

        try:
            await sandbox.files.write(code_path, request.code)
            await sandbox.files.write(
                runner_path,
                _PYTHON_RUNNER.format(
                    state_path=state_path,
                    code_path=code_path,
                    result_path=result_path,
                ),
            )
            outcome = await sandbox.commands.run(
                f"python3 {runner_path}",
                envs={item.name: item.value for item in request.environment},
                timeout=None,
            )
        except Exception as exc:
            raise _classify(exc) from exc

        result: str | None = None
        try:
            raw = await sandbox.files.read(result_path, format="text")
            result = raw if raw else None
        except Exception:
            # No trailing expression, or the run failed before writing one.
            result = None

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

    async def _find_any(self, sandbox_id: UUID) -> ProviderInstance | None:
        """The sandbox holding this workspace's files, whatever labelled it.

        Pre-consolidation sandboxes carry ``logical-id`` and
        ``managed-by=agentbox``. Those are live production workspaces, paused
        with the user's files inside them, so failing to match one would not
        merely duplicate compute -- it would hand the user an empty workspace
        and leave their work in a sandbox nothing points at any more.

        Paused sandboxes count as found. Pausing is how storage persists here.
        """
        namespace = self._config.metadata_namespace
        queries: list[dict[str, str]] = [
            {meta_sandbox_id(namespace): str(sandbox_id)}
        ]
        if self._config.adopt_legacy:
            queries.append(
                {
                    LEGACY_MANAGED_BY_KEY: LEGACY_MANAGED_BY,
                    LEGACY_LOGICAL_ID: str(sandbox_id),
                }
            )
        for query in queries:
            match = await self._first_matching(query)
            if match is not None:
                return match
        return None

    async def _first_matching(
        self, metadata: dict[str, str]
    ) -> ProviderInstance | None:
        from e2b.sandbox.sandbox_api import SandboxQuery

        try:
            paginator = self._sdk.list(
                query=SandboxQuery(metadata=metadata), **self._api()
            )
            matches = await paginator.next_items()
        except Exception as exc:
            raise _classify(exc) from exc

        # Prefer a running sandbox when several match, so a duplicate left by
        # an earlier failure does not shadow the one actually serving.
        ordered = sorted(
            matches,
            key=lambda info: str(info.state).lower().endswith("running"),
            reverse=True,
        )
        for info in ordered:
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


_PYTHON_RUNNER = """
import ast, pickle, os, sys

_STATE = {state_path!r}
_CODE = {code_path!r}
_RESULT = {result_path!r}

_ns = {{"__name__": "__main__"}}
if os.path.exists(_STATE):
    try:
        with open(_STATE, "rb") as handle:
            _ns.update(pickle.load(handle))
    except Exception:
        pass

with open(_CODE) as handle:
    _source = handle.read()

_tree = ast.parse(_source)
_tail = None
if _tree.body and isinstance(_tree.body[-1], ast.Expr):
    _tail = ast.Expression(_tree.body.pop().value)

try:
    exec(compile(_tree, "<session>", "exec"), _ns)
    if _tail is not None:
        _value = eval(compile(_tail, "<session>", "eval"), _ns)
        if _value is not None:
            with open(_RESULT, "w") as handle:
                handle.write(repr(_value) if not isinstance(_value, str) else _value)
finally:
    _keep = {{}}
    for _name, _value in list(_ns.items()):
        if _name.startswith("__"):
            continue
        try:
            pickle.dumps(_value)
        except Exception:
            continue
        _keep[_name] = _value
    try:
        with open(_STATE, "wb") as handle:
            pickle.dump(_keep, handle)
    except Exception:
        pass
"""



def _decode_pid(raw) -> tuple[int, bool]:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    pid, _, flag = text.partition(":")
    return int(pid), flag == "1"


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
