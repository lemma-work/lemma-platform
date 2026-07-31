from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from agentbox.domain import (
    PortProtocol,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRejected,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderInventoryAllocation,
    ProviderLifecycleError,
    ProviderMetadataEntry,
    ProviderNotReady,
    ProviderPortTarget,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.profiles import ProfileRegistry

from .docker import DockerSandboxAdapter, RuntimeCredentialSigner
from .workspace_runtime_client import WorkspaceRuntimeClient, WorkspaceRuntimeError


_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class LocalRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "local_runtime_failed",
        retryable: bool = True,
        status_code: int = 503,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class LocalRuntimeNotFound(LocalRuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LemmaLocalAdapterConfig:
    executable: str
    scope: str = "lemma-local:managed"
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
        if not self.scope:
            raise ValueError("managed runtime provider scope is required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("managed runtime timeout must be positive")


class LemmaLocalSandboxAdapter(DockerSandboxAdapter):
    """Provider-neutral AgentBox adapter for Lemma's private VZ/WSL guest.

    Lifecycle and inventory use the capability-authenticated native bridge.
    Once a workspace is running, process, PTY, Python, and filesystem calls use
    the same typed workspace-runtime protocol as the Docker adapter.
    """

    name = "lemma_local"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(
        self,
        profiles: ProfileRegistry,
        config: LemmaLocalAdapterConfig,
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
        self._profiles = profiles
        self._local_config = config
        self._runtime_credentials = runtime_credentials
        self._executable = resolved
        self.scope = config.scope

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        artifact = self._profiles.docker_artifact(
            request.profile,
            workload_kind=request.key.workload_kind,
        )
        if "@sha256:" not in artifact.image:
            raise ProviderCreateRejected(
                "managed runtime images must be pinned by sha256 digest"
            )
        sandbox_id = self._sandbox_id(
            request.key.workload_kind,
            request.key.logical_id.hex,
        )
        metadata = {item.name: item.value for item in request.metadata}
        workspace = request.key.workload_kind == WorkloadKind.WORKSPACE
        apps = (
            [
                self._app("runtime", 8080, "eager", "private"),
                self._app("browser", 4848, "lazy", "workspace_user"),
            ]
            if workspace
            else [self._app("function", 8090, "eager", "private")]
        )
        runtime_token = (
            self._runtime_credentials.token(sandbox_id) if workspace else None
        )
        try:
            snapshot = await self._request(
                "sandbox.ensure",
                {
                    "sandbox_id": sandbox_id,
                    "workload_kind": request.key.workload_kind.value,
                    "image": artifact.image,
                    "metadata": metadata,
                    "runtime_token": runtime_token,
                    "apps": apps,
                    "resources": {
                        "memory": (
                            self._local_config.workspace_memory
                            if workspace
                            else self._local_config.function_memory
                        ),
                        "cpus": (
                            self._local_config.workspace_cpus
                            if workspace
                            else self._local_config.function_cpus
                        ),
                    },
                    "callback": {
                        "required": self._local_config.callback_required,
                        "url": self._local_config.callback_url,
                        "health_path": self._local_config.callback_health_path,
                        "timeout_seconds": (
                            self._local_config.callback_timeout_seconds
                        ),
                    },
                },
                deadline_at=request.deadline_at,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderCreateAmbiguous("managed runtime create timed out") from exc
        except LocalRuntimeError as exc:
            if exc.retryable:
                raise ProviderCreateAmbiguous(str(exc)) from exc
            raise ProviderCreateRejected(str(exc)) from exc
        instance_id = self._instance_id(snapshot)
        return ProviderCreateResult(
            provider_id=sandbox_id,
            provider_instance_id=instance_id,
            provider_request_id=None,
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=sandbox_id,
                    bound_to_allocation=False,
                )
                if workspace
                else None
            ),
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        try:
            snapshot = await self._status(allocation.provider_id, deadline_at)
        except LocalRuntimeNotFound as exc:
            raise ProviderAllocationFailed(str(exc)) from exc
        status = self._status_object(snapshot)
        if not bool(status.get("ready")):
            lifecycle = str(status.get("status") or "unknown")
            if lifecycle in {"STOPPED", "ERROR"}:
                raise ProviderAllocationFailed(
                    f"managed runtime allocation is {lifecycle.lower()}"
                )
            raise ProviderNotReady(
                "managed runtime allocation is still starting",
                retry_after_ms=250,
            )
        metadata = snapshot.get("metadata")
        if not isinstance(metadata, dict) or (
            metadata.get("profile-name") != profile.name
            or metadata.get("profile-digest") != profile.digest
        ):
            raise ProviderAllocationFailed(
                "managed runtime profile metadata does not match allocation"
            )
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=self._instance_id(snapshot),
        )

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        if allocation.key.workload_kind == WorkloadKind.WORKSPACE:
            try:
                client = await self._runtime_client(
                    allocation.provider_id,
                    deadline_at=deadline_at,
                )
                try:
                    await client.quiesce(deadline_at=deadline_at)
                finally:
                    await client.close()
            except WorkspaceRuntimeError as exc:
                raise ProviderLifecycleError(str(exc)) from exc
        await self._mutate(
            "sandbox.release",
            allocation.provider_id,
            deadline_at=deadline_at,
        )

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        await self._mutate(
            "sandbox.delete",
            allocation.provider_id,
            deadline_at=deadline_at,
        )

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        *,
        deadline_at: datetime,
    ) -> None:
        await self._mutate(
            "sandbox.purge_storage",
            provider_storage_id,
            deadline_at=deadline_at,
        )

    async def find_allocations(
        self,
        metadata: tuple[ProviderMetadataEntry, ...],
        *,
        deadline_at: datetime,
    ) -> tuple[ProviderInventoryAllocation, ...]:
        expected = {item.name: item.value for item in metadata}
        try:
            result = await self._request(
                "sandbox.list",
                {},
                deadline_at=deadline_at,
            )
        except LocalRuntimeError as exc:
            raise ProviderLifecycleError(str(exc)) from exc
        raw_items = result.get("sandboxes")
        if not isinstance(raw_items, list):
            raise ProviderLifecycleError("managed runtime inventory is invalid")
        matches: list[ProviderInventoryAllocation] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            actual = item.get("metadata")
            if not isinstance(actual, dict) or any(
                actual.get(name) != value for name, value in expected.items()
            ):
                continue
            status = self._status_object(item)
            sandbox_id = str(status.get("id") or "")
            if not sandbox_id:
                continue
            workspace = expected.get("workload-kind") == WorkloadKind.WORKSPACE.value
            matches.append(
                ProviderInventoryAllocation(
                    provider_id=sandbox_id,
                    provider_instance_id=self._instance_id(item),
                    workspace_storage=(
                        ProviderStorageResult(
                            provider_storage_id=sandbox_id,
                            bound_to_allocation=False,
                        )
                        if workspace
                        else None
                    ),
                )
            )
        return tuple(matches)

    async def resolve_port_target(
        self,
        allocation: ProviderAllocationRef,
        *,
        port: int,
        protocol: PortProtocol,
        deadline_at: datetime,
        activity_until: datetime | None = None,
    ) -> ProviderPortTarget:
        del activity_until
        snapshot = await self._status(allocation.provider_id, deadline_at)
        status = self._status_object(snapshot)
        apps = status.get("apps")
        if not isinstance(apps, dict):
            raise ProviderLifecycleError("managed runtime omitted application endpoints")
        endpoint = next(
            (
                value.get("private_url")
                for value in apps.values()
                if isinstance(value, dict) and value.get("port") == port
            ),
            None,
        )
        if not isinstance(endpoint, str) or not endpoint:
            raise ProviderLifecycleError(
                f"managed runtime does not expose sandbox port {port}"
            )
        if protocol == PortProtocol.HTTPS:
            endpoint = endpoint.replace("http://", "https://", 1)
        return ProviderPortTarget(base_url=endpoint)

    async def close(self) -> None:
        return None

    async def _runtime_client(
        self,
        provider_id: str,
        *,
        deadline_at: datetime,
    ) -> WorkspaceRuntimeClient:
        snapshot = await self._status(provider_id, deadline_at)
        status = self._status_object(snapshot)
        runtime_url = status.get("runtime_url")
        if not isinstance(runtime_url, str) or not runtime_url:
            raise WorkspaceRuntimeError(
                "managed workspace runtime endpoint is unavailable"
            )
        return WorkspaceRuntimeClient(
            runtime_url,
            self._runtime_credentials.token(provider_id),
        )

    async def _status(
        self,
        sandbox_id: str,
        deadline_at: datetime,
    ) -> dict[str, Any]:
        return await self._request(
            "sandbox.status",
            {"sandbox_id": sandbox_id},
            deadline_at=deadline_at,
        )

    async def _mutate(
        self,
        operation: str,
        sandbox_id: str,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            await self._request(
                operation,
                {"sandbox_id": sandbox_id},
                deadline_at=deadline_at,
            )
        except LocalRuntimeNotFound:
            return
        except LocalRuntimeError as exc:
            raise ProviderLifecycleError(str(exc)) from exc

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
            raise LocalRuntimeError(
                "managed runtime request exceeds 1 MiB",
                retryable=False,
                status_code=422,
            )
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise asyncio.TimeoutError
        timeout = min(remaining, self._local_config.request_timeout_seconds)

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
            process = await asyncio.to_thread(invoke)
        except subprocess.TimeoutExpired as exc:
            raise asyncio.TimeoutError from exc
        if len(process.stdout.encode()) > _MAX_RESPONSE_BYTES:
            raise LocalRuntimeError("managed runtime response exceeds 4 MiB")
        try:
            response = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            diagnostic = process.stderr.splitlines()[:1]
            suffix = f": {diagnostic[0]}" if diagnostic else ""
            raise LocalRuntimeError(
                f"managed runtime response was not JSON{suffix}"
            ) from exc
        if not isinstance(response, dict):
            raise LocalRuntimeError("managed runtime response was not an object")
        if process.returncode != 0 or response.get("ok") is not True:
            error = response.get("error")
            details = error if isinstance(error, dict) else {}
            code = str(details.get("code") or "local_runtime_failed")
            error_type = (
                LocalRuntimeNotFound
                if code == "not_found"
                else LocalRuntimeError
            )
            raise error_type(
                str(details.get("message") or "managed runtime request failed"),
                code=code,
                retryable=bool(details.get("retryable", True)),
                status_code=int(details.get("status_code") or 503),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise LocalRuntimeError("managed runtime response omitted its result")
        return result

    @staticmethod
    def _app(
        name: str,
        port: int,
        startup: str,
        exposure: str,
    ) -> dict[str, object]:
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

    @staticmethod
    def _sandbox_id(workload_kind: WorkloadKind, logical_id_hex: str) -> str:
        prefix = "w" if workload_kind == WorkloadKind.WORKSPACE else "f"
        return f"{prefix}-{logical_id_hex}"

    @staticmethod
    def _status_object(snapshot: dict[str, Any]) -> dict[str, Any]:
        status = snapshot.get("status")
        if not isinstance(status, dict):
            raise ProviderLifecycleError("managed runtime status is invalid")
        return status

    @staticmethod
    def _instance_id(snapshot: dict[str, Any]) -> str | None:
        value = snapshot.get("provider_id")
        return value if isinstance(value, str) and value else None
