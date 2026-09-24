"""Lemma Desktop's local sandbox provider.

Desktop ships its own VZ (macOS) or WSL (Windows) guest and manages sandboxes
through a native bridge rather than a Docker socket. The bridge speaks one
JSON request per invocation over stdio, and is capability-authenticated by
being the executable Desktop installed -- there is no port to reach it on and
no credential to leak.

Two things make it fit the same seam as Docker and E2B without special cases:

*Its ensure is already idempotent.* `sandbox.ensure` either creates the guest
sandbox or returns the one that is there, which is the property the whole
provisioning design rests on.

*Its sandbox is its own storage.* The guest ties a workspace's disk to the
sandbox id, so like E2B this is SANDBOX_NATIVE: a new epoch adopts the same
sandbox rather than replacing it, because replacing it would take the user's
files with it.

Once a sandbox is running, processes, PTY, Python and filesystem all go through
the identical workspace-runtime protocol Docker uses -- the guest exposes the
same runtime on the same contract, so none of that code is duplicated here.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from time import monotonic
from pathlib import Path
from uuid import UUID


from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.lemma_local_bridge import (
    BridgeResult,
    call_bridge,
)
from app.modules.workspace.providers.lemma_local_config import (
    LemmaLocalProviderConfig,
    LocalBridgeError,
    LocalBridgeNotFound,
)
from app.modules.workspace.providers.lemma_local_snapshot import (
    guest_id_of as _guest_id_of,
    is_running as _is_running,
    is_serving as _is_serving,
    sandbox_id_from_guest_id as _sandbox_id_from_guest_id,
    state_of as _state_of,
)
from app.modules.workspace.providers.base import (
    ProviderCreateAmbiguous,
    ProviderCreateSpec,
    ProviderGone,
    ProviderInstance,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
)
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.profiles import profile_for
from app.modules.workspace.providers.lemma_local_ops import (
    LemmaLocalOpsMixin,
    _status_object,
)
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeClient,
)
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeError,
)


#: How long a sandbox's runtime address is believed without asking the guest
#: again. Seconds, not minutes: the win is collapsing the several round-trips a
#: single file operation makes, not remembering anything across a user's pause.
_RUNTIME_URL_TTL_SECONDS = 5.0


class LemmaLocalSandboxProvider(LemmaLocalOpsMixin):
    name = "lemma_local"
    # The guest binds a workspace's disk to its sandbox id, so the sandbox is
    # the storage and a replacement would destroy the user's files.
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE
    # The guest has no start: `sandbox.ensure` is create-or-replace, so a
    # stopped workspace comes back by being rebuilt against the same volume.
    # Waiting for one to resume waits forever -- and idle release stops the
    # container by design, so every desktop workspace broke three minutes after
    # its last use and stayed broken until something deleted the container.
    resumes_stopped_instances = False

    def __init__(
        self,
        config: LemmaLocalProviderConfig,
        runtime_credentials: RuntimeCredentialSigner,
    ) -> None:
        candidate = Path(config.executable).expanduser()
        resolved = (
            str(candidate.resolve())
            if candidate.is_file()
            else shutil.which(config.executable)
        )
        if resolved is None:
            raise RuntimeError(
                f"managed runtime bridge does not exist: {config.executable}"
            )
        self._config = config
        self._executable = resolved
        self._runtime_credentials = runtime_credentials
        # guest id -> (runtime url, monotonic expiry)
        self._runtime_urls: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @staticmethod
    def _guest_id(sandbox_id: UUID, kind: SandboxKind) -> str:
        """The id the guest knows this sandbox by.

        Deliberately without the epoch. The guest's sandbox owns the disk, so
        a new epoch must resolve to the same guest sandbox rather than a new
        one; the fence is the guest sandbox's own existence.
        """
        prefix = "w" if kind is SandboxKind.WORKSPACE else "f"
        return f"{prefix}-{sandbox_id.hex}"

    def _guest_id_from_name(self, name: str) -> tuple[str, SandboxKind] | None:
        parsed = naming.parse_container_name(name)
        if parsed is None:
            return None
        sandbox_id, kind, _ = parsed
        return self._guest_id(sandbox_id, kind), kind

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        profile = profile_for(spec.kind)
        image = spec.image or profile.image
        if "@sha256:" not in image:
            raise ProviderRejected(
                "managed runtime images must be pinned by sha256 digest"
            )

        guest_id = self._guest_id(spec.sandbox_id, spec.kind)
        existed = await self._find(guest_id, deadline_at=spec.deadline_at) is not None

        workspace = spec.kind is SandboxKind.WORKSPACE
        apps = (
            [
                _app("runtime", profile.runtime_port, "eager", "private"),
                _app("browser", 4848, "lazy", "workspace_user"),
                # The browser relay. `private`, because unlike the dashboard
                # nobody reaches this from a browser tab -- only the backend
                # does, holding the token it delivered. Declared here because
                # the guest publishes only the ports named at create, so an
                # undeclared one is not slow to reach, it is unreachable.
                _app("relay", 4850, "lazy", "private"),
            ]
            if workspace
            else [_app("function", profile.runtime_port, "eager", "private")]
        )
        try:
            snapshot = await call_bridge(
                self._executable,
                self._config.request_timeout_seconds,
                "sandbox.ensure",
                {
                    **spec.guest_grants(),
                    "sandbox_id": guest_id,
                    "workload_kind": spec.kind.value,
                    "image": image,
                    "metadata": {
                        "lemma-sandbox-id": str(spec.sandbox_id),
                        "lemma-sandbox-kind": spec.kind.value,
                        "lemma-epoch": str(spec.epoch),
                    },
                    # The caller's environment, which Docker and E2B both
                    # forward and this fabric used to drop. No caller sets one
                    # today -- `SandboxService` builds the spec without `env` --
                    # so this closes a contract gap rather than changing any
                    # running sandbox. The guest validates every entry and
                    # rejects the whole ensure on a bad one, which is the
                    # behaviour a caller that starts setting it will want.
                    "env": dict(spec.env),
                    "runtime_token": (
                        self._runtime_credentials.token(guest_id) if workspace else None
                    ),
                    "apps": apps,
                    "resources": {
                        "memory": (
                            self._config.workspace_memory
                            if workspace
                            else self._config.function_memory
                        ),
                        "cpus": (
                            self._config.workspace_cpus
                            if workspace
                            else self._config.function_cpus
                        ),
                    },
                    "callback": {
                        "required": self._config.callback_required,
                        "url": self._config.callback_url,
                        "health_path": self._config.callback_health_path,
                        "timeout_seconds": self._config.callback_timeout_seconds,
                    },
                },
                deadline_at=spec.deadline_at,
            )
        except asyncio.TimeoutError as exc:
            # The bridge may have completed the ensure after the timeout. It is
            # idempotent, so the next attempt resolves this by asking again.
            raise ProviderCreateAmbiguous("managed runtime create timed out") from exc
        except LocalBridgeError as exc:
            if exc.retryable:
                raise ProviderCreateAmbiguous(str(exc)) from exc
            raise ProviderRejected(str(exc)) from exc

        return ProviderInstance(
            provider_id=guest_id,
            name=spec.name,
            running=_is_running(snapshot),
            storage_adopted=existed,
        )

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        resolved = self._guest_id_from_name(name)
        if resolved is None:
            return None
        guest_id, _ = resolved
        snapshot = await self._find(guest_id, deadline_at=deadline_at)
        if snapshot is None:
            return None
        return ProviderInstance(
            provider_id=guest_id, name=name, running=_is_running(snapshot)
        )

    async def wait_ready(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """Confirm a just-created sandbox is serving.

        The bridge's ensure does not return until the sandbox is up, so this
        confirms once rather than looping: a guest that reports ready and is
        not reachable is a guest fault the caller should see, not something to
        wait out. Only ever reached for a *running* instance -- a stopped one
        is rebuilt by the service, because nothing here could start it.
        """
        # Imported here rather than at module scope, matching the workspace
        # branch below: `sandbox_runtime.errors` pulls in the in-sandbox runtime
        # package, which must not be a hard import of the provider.
        from sandbox_runtime.errors import SandboxUnavailable

        if kind is not SandboxKind.WORKSPACE:
            # A function sandbox is confirmed through the guest's own view.
            #
            # This used to return here, which made `verify_ready=True` from the
            # function resolver verify nothing at all: a sandbox that had been
            # created but was not yet serving was reported ready, and the
            # failure surfaced later as a runtime endpoint that never answered.
            # Nothing caught it, because none of the real-guest tests exercise a
            # FUNCTION sandbox -- they are all workspaces.
            #
            # Checked through `inspect`, which is a short status call, rather
            # than by holding the guest's single control channel open: that
            # channel serves one request at a time, and a long wait on it stalls
            # every other sandbox operation on the machine.
            snapshot = await self._find(instance.provider_id, deadline_at=deadline_at)
            if snapshot is None:
                raise SandboxUnavailable(
                    f"function sandbox {instance.provider_id} disappeared before "
                    "it was ready"
                )
            if not _is_serving(snapshot):
                raise SandboxUnavailable(
                    f"function sandbox {instance.provider_id} is not serving yet "
                    f"(state {_state_of(snapshot)})"
                )
            return
        # Converted here, not only in `runtime_scope`. `SandboxUnavailable` is
        # how this codebase spells "worth another go", and every retry the
        # platform has keys on it: the ensure loop's backoff, `with_backpressure`,
        # and the directory-ensure loop that sets `force_reconcile=True` and
        # rebuilds the container. A raw `WorkspaceRuntimeError` slips past all of
        # them and past `_fail()`, so the sandbox row stays PRESENT and the next
        # ensure takes the identical branch -- which is why a stopped container
        # produced four byte-identical failures in a row instead of being
        # rebuilt on the second.
        try:
            client = await self._runtime_client(
                instance.provider_id, deadline_at=deadline_at
            )
            try:
                await client.health(deadline_at=deadline_at)
            finally:
                await client.close()
        except ProviderGone:
            raise
        except (WorkspaceRuntimeError, LocalBridgeError) as exc:
            raise SandboxUnavailable(str(exc)) from exc

    async def release(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        await self._mutate(
            "sandbox.release", instance.provider_id, deadline_at=deadline_at
        )

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        resolved = self._guest_id_from_name(name)
        if resolved is None:
            return
        await self._mutate("sandbox.delete", resolved[0], deadline_at=deadline_at)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        """Always None: the guest binds the disk to the sandbox itself."""
        return None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str:
        raise ProviderRejected(
            "managed runtime storage lives with the sandbox; there is no volume"
        )

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        return None

    async def purge_storage(self, guest_id: str, *, deadline_at: datetime) -> None:
        await self._mutate("sandbox.purge_storage", guest_id, deadline_at=deadline_at)

    # ------------------------------------------------------------------
    # Reclamation
    # ------------------------------------------------------------------

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        try:
            listing = await call_bridge(
                self._executable,
                self._config.request_timeout_seconds,
                "sandbox.list",
                {},
                deadline_at=deadline_at,
            )
        except LocalBridgeError as exc:
            raise ProviderRejected(str(exc)) from exc

        entries = listing.get("sandboxes")
        if not isinstance(entries, list):
            return ()

        found: list[ProviderObject] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            guest_id = _guest_id_of(entry)
            if guest_id is None:
                # Without an id there is nothing the sweeper could act on, and
                # a placeholder would be worse than an omission: destroy()
                # would silently no-op on it forever.
                continue
            metadata = entry.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            raw_id = metadata.get("lemma-sandbox-id")
            sandbox_id = None
            if isinstance(raw_id, str):
                try:
                    sandbox_id = UUID(raw_id)
                except ValueError:
                    sandbox_id = None
            if sandbox_id is None:
                # Pre-consolidation guests carry no metadata, but their id is
                # `{w|f}-{hex}` and that is enough to identify the owner.
                sandbox_id = _sandbox_id_from_guest_id(guest_id)
            found.append(
                ProviderObject(
                    provider_id=guest_id,
                    name=guest_id,
                    sandbox_id=sandbox_id,
                    # The guest reuses one sandbox across epochs, so an epoch
                    # here would only ever be the current one.
                    epoch=None,
                    running=_is_running(entry),
                    legacy="lemma-sandbox-id" not in metadata,
                )
            )
        return tuple(found)

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Bridge plumbing
    # ------------------------------------------------------------------

    def _ops(self, instance: ProviderInstance, deadline_at: datetime):
        from contextlib import asynccontextmanager

        from sandbox_runtime.errors import (
            SandboxPathConflict,
            SandboxPathNotFound,
            SandboxProcessNotFound,
            SandboxRejected,
            SandboxUnauthorized,
            SandboxUnavailable,
        )
        from app.modules.workspace.providers.runtime_errors import (
            WorkspaceRuntimeFileConflict,
            WorkspaceRuntimeFileNotFound,
            WorkspaceRuntimeFileRejected,
            WorkspaceRuntimeProcessGone,
            WorkspaceRuntimeUnauthorized,
        )

        @asynccontextmanager
        async def scope():
            client: WorkspaceRuntimeClient | None = None
            try:
                client = await self._runtime_client(
                    instance.provider_id, deadline_at=deadline_at
                )
                yield client
            except WorkspaceRuntimeFileNotFound as exc:
                raise SandboxPathNotFound(str(exc)) from exc
            except WorkspaceRuntimeFileConflict as exc:
                raise SandboxPathConflict(str(exc)) from exc
            except WorkspaceRuntimeFileRejected as exc:
                # 413, 422 and 507: too big, not a path this runtime will take,
                # no room. Docker has mapped these to a refusal since they
                # existed and this did not, so on Desktop alone they fell
                # through to `SandboxUnavailable` below -- which
                # `with_backpressure` retries until the deadline. A file that
                # is too large, or a guest whose disk is full, became a retry
                # loop on the machine's single vsock control channel instead of
                # one sentence saying what was wrong.
                raise SandboxRejected(str(exc)) from exc
            except WorkspaceRuntimeProcessGone as exc:
                # Definitive, and about the process rather than the sandbox.
                # `ProviderGone` would make the client forget its handle to a
                # workspace that is fine; `SandboxUnavailable` would retry a
                # process that will never exist until the deadline.
                raise SandboxProcessNotFound(str(exc)) from exc
            except WorkspaceRuntimeUnauthorized as exc:
                # Definitive: this credential will not become valid by waiting.
                raise SandboxUnauthorized(str(exc)) from exc
            except ProviderGone:
                raise
            except asyncio.TimeoutError as exc:
                # The bridge stopped answering within the deadline. Retryable,
                # but it has to arrive as a sandbox error with a sentence in it:
                # uncaught, it left this scope as a bare `TimeoutError` and
                # every caller rendered it as `500 INTERNAL_ERROR` with a null
                # message, which is what a five-minute file listing looked like.
                self._forget_runtime_url(instance.provider_id)
                raise SandboxUnavailable(
                    "managed runtime did not answer before the deadline"
                ) from exc
            except (WorkspaceRuntimeError, LocalBridgeError) as exc:
                # Anything that failed at the transport may mean the sandbox
                # moved. Cheaper to ask the guest again next time than to keep
                # dialling an address that has stopped answering.
                self._forget_runtime_url(instance.provider_id)
                raise SandboxUnavailable(str(exc)) from exc
            finally:
                if client is not None:
                    await client.close()

        return scope()

    async def _runtime_client(
        self, guest_id: str, *, deadline_at: datetime
    ) -> WorkspaceRuntimeClient:
        runtime_url = self._remembered_runtime_url(guest_id)
        if runtime_url is None:
            snapshot = await self._status(guest_id, deadline_at=deadline_at)
            runtime_url = _status_object(snapshot).get("runtime_url")
            if not isinstance(runtime_url, str) or not runtime_url:
                raise WorkspaceRuntimeError(
                    "managed workspace runtime endpoint is unavailable"
                )
            self._runtime_urls[guest_id] = (
                runtime_url,
                monotonic() + _RUNTIME_URL_TTL_SECONDS,
            )
        return WorkspaceRuntimeClient(
            runtime_url, self._runtime_credentials.token(guest_id)
        )

    def _remembered_runtime_url(self, guest_id: str) -> str | None:
        """Where this sandbox's runtime was, if that was true a moment ago.

        Every workspace operation entered `_ops`, and `_ops` asked the guest
        where the runtime is before doing anything -- a `hostctl` fork on the
        host, a vsock round-trip, and a `nerdctl inspect` fork inside the VM,
        all to recover a URL that had not changed. `_ops` has seventeen call
        sites, so a multi-step file operation paid that once per step.

        In-process rather than Redis, which is the rule for cached *data*: this
        is the address of a container on this machine, it is worthless to any
        other process, and a Redis round-trip to avoid a local one would cost
        more than it saved. The window is seconds, and a stale entry is dropped
        the moment an operation fails against it, so a recreated sandbox costs
        one retry rather than a wrong answer.
        """
        remembered = self._runtime_urls.get(guest_id)
        if remembered is None:
            return None
        url, expires_at = remembered
        if monotonic() >= expires_at:
            self._runtime_urls.pop(guest_id, None)
            return None
        return url

    def _forget_runtime_url(self, guest_id: str) -> None:
        self._runtime_urls.pop(guest_id, None)

    async def _find(
        self, guest_id: str, *, deadline_at: datetime
    ) -> BridgeResult | None:
        """Absence, reported as absence.

        ``_status`` turns not-found into ``ProviderGone`` because a caller
        holding a handle needs that to be definitive. Here the question is
        merely "is there one?", so the same answer is a None rather than a
        failure.
        """
        try:
            return await self._status(guest_id, deadline_at=deadline_at)
        except ProviderGone:
            return None
        except LocalBridgeError:
            return None

    async def _status(self, guest_id: str, *, deadline_at: datetime) -> BridgeResult:
        try:
            return await call_bridge(
                self._executable,
                self._config.request_timeout_seconds,
                "sandbox.status",
                {"sandbox_id": guest_id},
                deadline_at=deadline_at,
            )
        except LocalBridgeNotFound as exc:
            raise ProviderGone(str(exc)) from exc

    async def _mutate(
        self, operation: str, guest_id: str, *, deadline_at: datetime
    ) -> None:
        try:
            await call_bridge(
                self._executable,
                self._config.request_timeout_seconds,
                operation,
                {"sandbox_id": guest_id},
                deadline_at=deadline_at,
            )
        except LocalBridgeNotFound:
            # Already absent is the outcome these operations were asking for.
            return
        except LocalBridgeError as exc:
            raise ProviderRejected(str(exc)) from exc


def _app(name: str, port: int, startup: str, exposure: str) -> dict[str, object]:
    return {
        "name": name,
        "public_slug": name,
        "port": port,
        "health_path": "/healthz" if name == "function" else "/health",
        "startup": startup,
        "exposure": exposure,
        "auth_mode": (
            "workspace_access_token"
            if exposure == "workspace_user"
            else "manager_api_key"
        ),
    }
