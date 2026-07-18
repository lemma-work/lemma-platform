from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException, status
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from agentbox.apps import SANDBOX_APPS, SandboxAppSpec
from agentbox.config import settings
from agentbox.sandbox_ids import sandbox_pod_name
from agentbox.schemas import (
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)
from agentbox.to_thread import run_sync
from agentbox.providers.legacy import LegacyRuntimeProviderMixin
from agentbox.providers.errors import ProviderError
from agentbox.providers.models import (
    EndpointProtocol,
    ManagedSandbox,
    ProviderCapabilities,
    SandboxEndpoint,
    SandboxRef,
)
from agentbox.runtime_proxy import (
    _MAX_RUNTIME_ERROR_BODY_LENGTH as _SHARED_MAX_RUNTIME_ERROR_BODY_LENGTH,
    request_runtime_json,
)

from agentbox.observability import get_logger

logger = get_logger(__name__)
_MAX_RUNTIME_ERROR_BODY_LENGTH = _SHARED_MAX_RUNTIME_ERROR_BODY_LENGTH


def _request_runtime_json(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Deprecated compatibility wrapper around the shared runtime transport."""

    try:
        return request_runtime_json(*args, **kwargs)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        runtime_status = detail.get("runtime_status")
        if runtime_status is not None:
            logger.debug('agentbox.kubernetes.runtime_returned_http_s.diagnostic', runtime_status=runtime_status)
        elif detail.get("error") == "runtime returned malformed JSON":
            logger.debug('agentbox.kubernetes.runtime_returned_malformed_json.diagnostic')
        raise


def _provider_error(exc: ApiException, operation: str) -> ProviderError:
    status_code = int(exc.status or 0)
    if not 400 <= status_code < 600:
        status_code = 502
    reason = exc.reason or "Kubernetes API request failed"
    return ProviderError(
        f"Kubernetes {operation} failed: {reason}",
        code="kubernetes_api_error",
        retryable=status_code in {409, 429} or status_code >= 500,
        status_code=status_code,
    )


class SandboxKubernetesClient(LegacyRuntimeProviderMixin):
    provider_name = "kubernetes"
    capabilities = ProviderCapabilities(
        stable_release_identity=False,
        release_preserves_filesystem=False,
        private_egress_isolation=True,
        authenticated_http=False,
        authenticated_websocket=False,
    )

    def __init__(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_v1 = client.CoreV1Api()

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        pod_name = sandbox_pod_name(sandbox_id)
        try:
            pod = await run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise HTTPException(
                    status_code=404, detail="Sandbox not found"
                ) from exc
            raise _provider_error(exc, "sandbox status") from exc

        return self._status_from_pod(sandbox_id, pod)

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        pod_name = sandbox_pod_name(sandbox_id)

        try:
            existing = await run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise _provider_error(exc, "sandbox lookup") from exc
            existing = None

        if existing is not None:
            status_obj = self._status_from_pod(sandbox_id, existing)
            if status_obj.ready:
                return status_obj

            phase = existing.status.phase if existing.status else None
            if phase in {"Pending", "Running"}:
                # Pod is still coming up (or running but not yet passing its
                # readiness probe); wait for it rather than recreating.
                return await self.wait_until_running(sandbox_id)

            # Terminal pod: the runtime pod uses restart_policy=Never, so a
            # Failed/Succeeded/Unknown pod can never become ready again. Recreate
            # it from scratch — `ensure` stays idempotent and self-healing, and
            # the sandbox's persistent record in the state store is left intact.
            logger.debug('agentbox.kubernetes.recreating_sandbox_s_existing_pod.diagnostic', sandbox_id=sandbox_id)
            await self.delete(sandbox_id)
            await self.wait_until_deleted(sandbox_id)

        pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=settings.agentbox_namespace,
                labels={
                    "app.kubernetes.io/name": "agentbox-sandbox",
                    "agentbox.work/sandbox-id": sandbox_id,
                    "agentbox.work/provider": self.provider_name,
                },
            ),
            spec=client.V1PodSpec(
                runtime_class_name=settings.agentbox_runtime_class_name,
                restart_policy="Never",
                node_selector={
                    "pool": settings.agentbox_node_selector_pool,
                },
                tolerations=[
                    client.V1Toleration(
                        key="workload",
                        operator="Equal",
                        value="sandbox",
                        effect="NoSchedule",
                    )
                ],
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=settings.agentbox_runtime_image,
                        image_pull_policy=settings.agentbox_sandbox_image_pull_policy,
                        ports=[
                            client.V1ContainerPort(
                                container_port=app.port,
                                name=app.name.replace("_", "-")[:15],
                            )
                            for app in SANDBOX_APPS.values()
                        ],
                        readiness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(
                                path="/health",
                                port=settings.agentbox_runtime_port,
                            ),
                            period_seconds=1,
                            timeout_seconds=1,
                            failure_threshold=120,
                        ),
                        env=[
                            client.V1EnvVar(name=name, value=value)
                            for name, value in sorted(request.env.items())
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={
                                "cpu": settings.agentbox_sandbox_cpu_request,
                                "memory": settings.agentbox_sandbox_memory_request,
                                "ephemeral-storage": settings.agentbox_sandbox_ephemeral_request,
                            },
                            limits={
                                "cpu": settings.agentbox_sandbox_cpu_limit,
                                "memory": settings.agentbox_sandbox_memory_limit,
                                "ephemeral-storage": settings.agentbox_sandbox_ephemeral_limit,
                            },
                        ),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                            run_as_non_root=True,
                        ),
                    )
                ],
                security_context=client.V1PodSecurityContext(
                    run_as_non_root=True,
                    seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                ),
            ),
        )

        try:
            created = await run_sync(
                self.core_v1.create_namespaced_pod,
                namespace=settings.agentbox_namespace,
                body=pod,
            )
        except ApiException as exc:
            if exc.status == 409:
                status_obj = await self.get_status(sandbox_id)
                if not status_obj.ready:
                    return await self.wait_until_running(sandbox_id)
                return status_obj
            raise _provider_error(exc, "sandbox creation") from exc
        del created
        return await self.wait_until_running(sandbox_id)

    async def wait_until_running(self, sandbox_id: str) -> SandboxInternalStatus:
        deadline = time.monotonic() + settings.agentbox_sandbox_ready_timeout_seconds
        last_status = None

        while time.monotonic() < deadline:
            status_obj = await self.get_status(sandbox_id)
            last_status = status_obj
            if status_obj.ready:
                return status_obj
            await asyncio.sleep(1)

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "message": "Sandbox did not become ready before timeout",
                "last_status": last_status.model_dump() if last_status else None,
            },
        )

    async def wait_until_deleted(self, sandbox_id: str) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                await self.get_status(sandbox_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return
                raise
            await asyncio.sleep(0.5)
        raise HTTPException(status_code=504, detail="Sandbox deletion did not complete")

    async def delete(self, sandbox_id: str) -> bool:
        pod_name = sandbox_pod_name(sandbox_id)
        try:
            await run_sync(
                self.core_v1.delete_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
                grace_period_seconds=0,
            )
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise _provider_error(exc, "sandbox deletion") from exc

    async def release(self, sandbox_id: str) -> bool:
        """Kubernetes cannot suspend a pod, so release its compute by deleting it."""

        return await self.delete(sandbox_id)

    async def list_managed(self) -> list[ManagedSandbox]:
        try:
            pod_list = await run_sync(
                self.core_v1.list_namespaced_pod,
                namespace=settings.agentbox_namespace,
                label_selector="app.kubernetes.io/name=agentbox-sandbox",
            )
        except ApiException as exc:
            raise _provider_error(exc, "sandbox inventory") from exc
        managed: list[ManagedSandbox] = []
        for pod in pod_list.items or []:
            labels = dict(pod.metadata.labels or {})
            sandbox_id = labels.get("agentbox.work/sandbox-id")
            if not sandbox_id:
                continue
            provider_id = str(pod.metadata.uid or pod.metadata.name)
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id=sandbox_id, provider_id=provider_id),
                    status=self._status_from_pod(sandbox_id, pod),
                    instance_id=provider_id,
                    metadata=labels,
                )
            )
        return managed

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint:
        del protocol
        pod_name = sandbox_pod_name(sandbox_id)
        try:
            pod = await run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise HTTPException(
                    status_code=404, detail="Sandbox not found"
                ) from exc
            raise _provider_error(exc, "endpoint resolution") from exc
        status_obj = self._status_from_pod(sandbox_id, pod)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        app_status = status_obj.apps.get(app.name)
        base_url = app_status.private_url if app_status else None
        if app.name == "runtime" and not base_url:
            base_url = status_obj.runtime_url
        if not base_url:
            raise HTTPException(
                status_code=409, detail="Sandbox app endpoint is missing"
            )
        provider_id = getattr(getattr(pod, "metadata", None), "uid", None)
        return SandboxEndpoint(
            base_url=base_url,
            instance_id=str(provider_id) if provider_id else status_obj.pod_ip,
        )

    def _status_from_pod(
        self, sandbox_id: str, pod: client.V1Pod
    ) -> SandboxInternalStatus:
        ready = False
        for status_obj in pod.status.container_statuses or []:
            if status_obj.name == "sandbox" and status_obj.ready:
                ready = True
                break

        return SandboxInternalStatus(
            id=sandbox_id,
            status=self._lifecycle_status(pod.status.phase, ready),
            ready=ready,
            pod_ip=pod.status.pod_ip,
            runtime_url=f"http://{pod.status.pod_ip}:{settings.agentbox_runtime_port}"
            if pod.status.pod_ip
            else None,
            apps=self._app_statuses_from_pod_ip(pod.status.pod_ip),
        )

    def _lifecycle_status(self, phase: str | None, ready: bool) -> str:
        if ready:
            return "RUNNING"
        if phase in {"Pending"}:
            return "CREATING"
        if phase in {"Succeeded"}:
            return "STOPPED"
        return "ERROR"

    def _app_statuses_from_pod_ip(
        self,
        pod_ip: str | None,
    ) -> dict[str, SandboxInternalAppStatus]:
        return {
            app.name: SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=bool(pod_ip),
                private_url=f"http://{pod_ip}:{app.port}" if pod_ip else None,
            )
            for app in SANDBOX_APPS.values()
        }
