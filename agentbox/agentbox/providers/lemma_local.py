from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from urllib import parse

from fastapi import HTTPException

from agentbox.apps import SANDBOX_APPS, SandboxAppSpec
from agentbox.config import settings
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus
from agentbox.to_thread import run_sync

from .errors import ProviderError
from .legacy import LegacyRuntimeProviderMixin
from .models import (
    EndpointProtocol,
    ManagedSandbox,
    ProviderCapabilities,
    SandboxEndpoint,
    SandboxRef,
)

_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _Snapshot:
    provider_id: str
    status: SandboxInternalStatus
    metadata: dict[str, str]


class LemmaLocalSandboxProvider(LegacyRuntimeProviderMixin):
    """AgentBox compute backed by Lemma's private Linux runtime.

    The backend owns lifecycle intent and durable state. A narrow, bundled
    host bridge translates these requests to either the app-owned macOS VM or
    the private Windows WSL distribution. No Docker-compatible socket is
    exposed to the backend process.
    """

    provider_name = "lemma_local"
    capabilities = ProviderCapabilities(
        stable_release_identity=True,
        release_preserves_filesystem=True,
        private_egress_isolation=False,
        authenticated_http=False,
        authenticated_websocket=False,
    )

    def __init__(self) -> None:
        configured = settings.agentbox_local_runtime_cli.strip()
        executable = shutil.which(configured)
        if executable is None:
            raise RuntimeError(
                "AGENTBOX_PROVIDER=lemma_local requires the bundled "
                f"runtime bridge {configured!r}"
            )
        self.executable = executable

    async def create(
        self,
        sandbox_id: str,
        request_obj: SandboxEnsureRequest,
    ) -> SandboxInternalStatus:
        sandbox_id = validate_sandbox_id(sandbox_id)
        self._validate_callback(request_obj)
        result = await self._request(
            "sandbox.ensure",
            {
                "sandbox_id": sandbox_id,
                "image": settings.agentbox_runtime_image,
                "env": request_obj.env,
                "apps": [app.model_dump() for app in SANDBOX_APPS.values()],
                "resources": {
                    "memory": settings.agentbox_memory_limit,
                    "cpus": settings.agentbox_cpu_limit,
                },
                "callback": {
                    "required": settings.agentbox_require_callback,
                    "health_path": settings.agentbox_callback_health_path,
                    "timeout_seconds": (
                        settings.agentbox_callback_ready_timeout_seconds
                    ),
                },
            },
        )
        return self._snapshot(result).status

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        return (await self._get_snapshot(sandbox_id)).status

    async def list_managed(self) -> list[ManagedSandbox]:
        result = await self._request("sandbox.list", {})
        items = result.get("sandboxes")
        if not isinstance(items, list):
            raise self._invalid_response("sandbox.list omitted sandboxes")
        managed: list[ManagedSandbox] = []
        for item in items:
            snapshot = self._snapshot(item)
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(
                        sandbox_id=snapshot.status.id,
                        provider_id=snapshot.provider_id,
                    ),
                    status=snapshot.status,
                    instance_id=snapshot.provider_id,
                    metadata=snapshot.metadata,
                )
            )
        return managed

    async def delete(self, sandbox_id: str) -> bool:
        sandbox_id = validate_sandbox_id(sandbox_id)
        result = await self._request(
            "sandbox.delete", {"sandbox_id": sandbox_id}, allow_not_found=True
        )
        return bool(result.get("deleted"))

    async def purge_storage(self, sandbox_id: str) -> bool:
        sandbox_id = validate_sandbox_id(sandbox_id)
        result = await self._request(
            "sandbox.purge_storage",
            {"sandbox_id": sandbox_id},
            allow_not_found=True,
        )
        return bool(result.get("purged"))

    async def release(self, sandbox_id: str) -> bool:
        sandbox_id = validate_sandbox_id(sandbox_id)
        result = await self._request(
            "sandbox.release", {"sandbox_id": sandbox_id}, allow_not_found=True
        )
        return bool(result.get("released"))

    async def adopt(self, sandbox_id: str, provider_id: str) -> bool:
        try:
            snapshot = await self._get_snapshot(sandbox_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return False
            raise
        return snapshot.provider_id == provider_id

    async def purge_managed(self, ref: SandboxRef) -> bool:
        result = await self._request(
            "sandbox.purge",
            {
                "sandbox_id": validate_sandbox_id(ref.sandbox_id),
                "provider_id": ref.provider_id,
            },
            allow_not_found=True,
        )
        return bool(result.get("purged"))

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint:
        del protocol
        snapshot = await self._get_snapshot(sandbox_id)
        if not snapshot.status.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        app_status = snapshot.status.apps.get(app.name)
        base_url = app_status.private_url if app_status else None
        if app.name == "runtime" and not base_url:
            base_url = snapshot.status.runtime_url
        if not base_url:
            raise HTTPException(
                status_code=409, detail="Sandbox app endpoint is missing"
            )
        return SandboxEndpoint(
            base_url=base_url,
            provider_id=snapshot.provider_id,
            instance_id=snapshot.provider_id,
        )

    async def _get_snapshot(self, sandbox_id: str) -> _Snapshot:
        sandbox_id = validate_sandbox_id(sandbox_id)
        result = await self._request("sandbox.status", {"sandbox_id": sandbox_id})
        return self._snapshot(result)

    async def _request(
        self,
        operation: str,
        parameters: dict[str, object],
        *,
        allow_not_found: bool = False,
    ) -> dict[str, object]:
        encoded = json.dumps(
            {"version": 1, "operation": operation, "parameters": parameters},
            separators=(",", ":"),
        )
        if len(encoded.encode()) > _MAX_REQUEST_BYTES:
            raise ProviderError(
                "Managed runtime request is too large",
                code="local_runtime_request_too_large",
                retryable=False,
                status_code=422,
            )

        def invoke() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.executable, "request"],
                input=f"{encoded}\n",
                capture_output=True,
                text=True,
                timeout=settings.agentbox_local_runtime_timeout_seconds,
                check=False,
            )

        try:
            process = await run_sync(invoke)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                "Managed runtime request timed out",
                code="local_runtime_timeout",
                retryable=True,
                status_code=504,
            ) from exc
        if len(process.stdout.encode()) > _MAX_RESPONSE_BYTES:
            raise self._invalid_response("response exceeded 4 MiB")
        try:
            response = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise self._invalid_response("response was not JSON") from exc
        if not isinstance(response, dict):
            raise self._invalid_response("response was not an object")
        if process.returncode != 0 or response.get("ok") is not True:
            error = response.get("error")
            error_data = error if isinstance(error, dict) else {}
            code = str(error_data.get("code") or "local_runtime_failed")
            if code == "not_found" and allow_not_found:
                return {}
            if code == "not_found":
                raise HTTPException(status_code=404, detail="Sandbox not found")
            message = str(error_data.get("message") or "Managed runtime request failed")
            raise ProviderError(
                message,
                code=code,
                retryable=bool(error_data.get("retryable", True)),
                status_code=int(error_data.get("status_code") or 503),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise self._invalid_response("response omitted result")
        return result

    def _snapshot(self, value: object) -> _Snapshot:
        if not isinstance(value, dict):
            raise self._invalid_response("sandbox snapshot was not an object")
        provider_id = value.get("provider_id")
        status = value.get("status")
        if not isinstance(provider_id, str) or not provider_id:
            raise self._invalid_response("sandbox snapshot omitted provider_id")
        try:
            parsed_status = SandboxInternalStatus.model_validate(status)
        except ValueError as exc:
            raise self._invalid_response("sandbox status was invalid") from exc
        metadata = value.get("metadata")
        metadata_data = metadata if isinstance(metadata, dict) else {}
        return _Snapshot(
            provider_id=provider_id,
            status=parsed_status,
            metadata={str(key): str(item) for key, item in metadata_data.items()},
        )

    def _validate_callback(self, request_obj: SandboxEnsureRequest) -> None:
        if not settings.agentbox_require_callback:
            return
        base_url = request_obj.env.get("LEMMA_BASE_URL", "").strip()
        parsed = parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(
                status_code=422,
                detail="Local sandbox requires an HTTP(S) LEMMA_BASE_URL",
            )

    @staticmethod
    def _invalid_response(detail: str) -> ProviderError:
        return ProviderError(
            f"Managed runtime returned an invalid response: {detail}",
            code="local_runtime_invalid_response",
            retryable=True,
            status_code=502,
        )
