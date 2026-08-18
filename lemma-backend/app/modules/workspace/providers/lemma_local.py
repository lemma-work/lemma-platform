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
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
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
from app.core.concurrency.offload import run_blocking
from app.modules.workspace.providers.lemma_local_ops import (
    LemmaLocalOpsMixin,
    _status_object,
)
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeClient,
    WorkspaceRuntimeError,
)

# The bridge is a local process, so these bound a malfunctioning one rather
# than a hostile one.
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class LocalBridgeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "local_runtime_failed",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LocalBridgeNotFound(LocalBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class LemmaLocalProviderConfig:
    executable: str
    request_timeout_seconds: float = 600
    workspace_memory: str = "2g"
    workspace_cpus: str = "2"
    function_memory: str = "2g"
    function_cpus: str = "4"
    callback_required: bool = False
    callback_url: str | None = None
    callback_health_path: str = "/health"
    callback_timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if not self.executable:
            raise ValueError("managed runtime bridge executable is required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("managed runtime timeout must be positive")


class LemmaLocalSandboxProvider(LemmaLocalOpsMixin):
    name = "lemma_local"
    # The guest binds a workspace's disk to its sandbox id, so the sandbox is
    # the storage and a replacement would destroy the user's files.
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE

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
            ]
            if workspace
            else [_app("function", profile.runtime_port, "eager", "private")]
        )
        try:
            snapshot = await self._request(
                "sandbox.ensure",
                {
                    "sandbox_id": guest_id,
                    "workload_kind": spec.kind.value,
                    "image": image,
                    "metadata": {
                        "lemma-sandbox-id": str(spec.sandbox_id),
                        "lemma-sandbox-kind": spec.kind.value,
                        "lemma-epoch": str(spec.epoch),
                    },
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
        """The bridge's ensure does not return until the sandbox is serving.

        Health is confirmed once here rather than looped, because a guest that
        reports ready and is not reachable is a guest fault the caller should
        see, not something to wait out.
        """
        if kind is not SandboxKind.WORKSPACE:
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
        from sandbox_runtime.errors import SandboxUnavailable

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
            listing = await self._request("sandbox.list", {}, deadline_at=deadline_at)
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
            SandboxUnavailable,
        )
        from app.modules.workspace.providers.runtime_client import (
            WorkspaceRuntimeFileConflict,
            WorkspaceRuntimeFileNotFound,
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
            except ProviderGone:
                raise
            except (WorkspaceRuntimeError, LocalBridgeError) as exc:
                raise SandboxUnavailable(str(exc)) from exc
            finally:
                if client is not None:
                    await client.close()

        return scope()

    async def _runtime_client(
        self, guest_id: str, *, deadline_at: datetime
    ) -> WorkspaceRuntimeClient:
        snapshot = await self._status(guest_id, deadline_at=deadline_at)
        runtime_url = _status_object(snapshot).get("runtime_url")
        if not isinstance(runtime_url, str) or not runtime_url:
            raise WorkspaceRuntimeError(
                "managed workspace runtime endpoint is unavailable"
            )
        return WorkspaceRuntimeClient(
            runtime_url, self._runtime_credentials.token(guest_id)
        )

    async def _find(
        self, guest_id: str, *, deadline_at: datetime
    ) -> dict[str, Any] | None:
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

    async def _status(self, guest_id: str, *, deadline_at: datetime) -> dict[str, Any]:
        try:
            return await self._request(
                "sandbox.status", {"sandbox_id": guest_id}, deadline_at=deadline_at
            )
        except LocalBridgeNotFound as exc:
            raise ProviderGone(str(exc)) from exc

    async def _mutate(
        self, operation: str, guest_id: str, *, deadline_at: datetime
    ) -> None:
        try:
            await self._request(
                operation, {"sandbox_id": guest_id}, deadline_at=deadline_at
            )
        except LocalBridgeNotFound:
            # Already absent is the outcome these operations were asking for.
            return
        except LocalBridgeError as exc:
            raise ProviderRejected(str(exc)) from exc

    async def _request(
        self,
        operation: str,
        parameters: dict[str, object],
        *,
        deadline_at: datetime,
    ) -> dict[str, Any]:
        encoded = json.dumps(
            {"version": 1, "operation": operation, "parameters": parameters},
            separators=(",", ":"),
        )
        if len(encoded.encode()) > _MAX_REQUEST_BYTES:
            raise LocalBridgeError(
                "managed runtime request exceeds 1 MiB", retryable=False
            )
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise asyncio.TimeoutError
        timeout = min(remaining, self._config.request_timeout_seconds)

        def invoke() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self._executable, "request"],
                input=f"{encoded}\n",
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        try:
            # The bridge is a blocking subprocess, so it is run off the event
            # loop; leaving it inline would stall every other request. Its own
            # limiter rather than ``external_http``: this call is bounded by the
            # request deadline, not an HTTP timeout, so a burst of long sandbox
            # operations would otherwise hold every slot the connector SDKs use.
            process = await run_blocking(invoke, limiter="local_bridge")
        except subprocess.TimeoutExpired as exc:
            raise asyncio.TimeoutError from exc

        if len(process.stdout.encode()) > _MAX_RESPONSE_BYTES:
            raise LocalBridgeError("managed runtime response exceeds 4 MiB")
        try:
            response = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            diagnostic = process.stderr.splitlines()[:1]
            suffix = f": {diagnostic[0]}" if diagnostic else ""
            raise LocalBridgeError(
                f"managed runtime response was not JSON{suffix}"
            ) from exc
        if not isinstance(response, dict):
            raise LocalBridgeError("managed runtime response was not an object")

        if process.returncode != 0 or response.get("ok") is not True:
            error = response.get("error")
            details = error if isinstance(error, dict) else {}
            code = str(details.get("code") or "local_runtime_failed")
            failure = LocalBridgeNotFound if code == "not_found" else LocalBridgeError
            raise failure(
                str(details.get("message") or "managed runtime request failed"),
                code=code,
                retryable=bool(details.get("retryable", True)),
            )

        result = response.get("result")
        if not isinstance(result, dict):
            raise LocalBridgeError("managed runtime response omitted its result")
        return result


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


def _is_running(snapshot: dict[str, Any]) -> bool:
    status = snapshot.get("status")
    if isinstance(status, dict):
        state = status.get("state") or status.get("status")
        return str(state).lower() in {"running", "ready"}
    return str(snapshot.get("state", "")).lower() in {"running", "ready"}


def _guest_id_of(entry: dict[str, Any]) -> str | None:
    """The guest id of one `sandbox.list` entry.

    A list entry wraps the snapshot: the id lives at ``status.id``, while
    ``sandbox.status`` returns that snapshot unwrapped. Both shapes are read
    here so a caller never has to know which call produced the dict.
    """

    status = entry.get("status")
    if isinstance(status, dict):
        nested = status.get("id")
        if isinstance(nested, str) and nested:
            return nested
    for key in ("sandbox_id", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _sandbox_id_from_guest_id(guest_id: str) -> UUID | None:
    prefix, _, raw = guest_id.partition("-")
    if prefix not in {"w", "f"}:
        return None
    try:
        return UUID(hex=raw)
    except ValueError:
        return None
