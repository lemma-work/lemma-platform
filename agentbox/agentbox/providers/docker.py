from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib import error, request

from fastapi import HTTPException

from agentbox.apps import SANDBOX_APPS, SandboxAppSpec
from agentbox.config import settings
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import (
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)
from agentbox.to_thread import run_sync

from .legacy import LegacyRuntimeProviderMixin
from .models import (
    EndpointProtocol,
    ManagedSandbox,
    ProviderCapabilities,
    SandboxEndpoint,
    SandboxRef,
)


def docker_container_name(sandbox_id: str) -> str:
    return f"agentbox-{validate_sandbox_id(sandbox_id)}"


class DockerSandboxProvider(LegacyRuntimeProviderMixin):
    cli_name = "docker"
    namespace = "docker"
    provider_name = "docker"
    capabilities = ProviderCapabilities(
        stable_release_identity=True,
        release_preserves_filesystem=True,
        private_egress_isolation=False,
        authenticated_http=False,
        authenticated_websocket=False,
    )

    def __init__(self) -> None:
        if not shutil.which(self.cli_name):
            raise RuntimeError(
                f"AGENTBOX_PROVIDER={self.namespace} requires the {self.cli_name} CLI"
            )
        self.storage_root = Path(self._storage_root_config()).expanduser()
        self.storage_host_root = self._storage_host_root()
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def container_name(self, sandbox_id: str) -> str:
        return docker_container_name(sandbox_id)

    def _storage_root_config(self) -> str:
        return settings.agentbox_storage_root

    def _storage_host_root_config(self) -> str | None:
        return settings.agentbox_storage_host_root

    def _storage_host_root(self) -> Path:
        host_root = self._storage_host_root_config()
        if not host_root:
            return self.storage_root
        return Path(host_root).expanduser()

    def _endpoint_host(self) -> str:
        return settings.agentbox_endpoint_host

    def _network_config(self) -> str | None:
        return settings.agentbox_network

    def _add_host_gateway_config(self) -> bool:
        return settings.agentbox_add_host_gateway

    def _selinux_enabled(self) -> bool:
        return Path("/sys/fs/selinux/enforce").exists()

    def _platform_config(self) -> str | None:
        return settings.agentbox_platform

    def _memory_limit_config(self) -> str | None:
        return settings.agentbox_memory_limit

    def _cpu_limit_config(self) -> str | None:
        return settings.agentbox_cpu_limit

    def _e2e_label_config(self) -> bool:
        return settings.agentbox_e2e_label

    async def create(
        self,
        sandbox_id: str,
        request_obj: SandboxEnsureRequest,
    ) -> SandboxInternalStatus:
        validate_sandbox_id(sandbox_id)
        existing = await self._inspect_sandbox(sandbox_id)
        if existing is not None:
            if not existing.ready:
                await self._run_docker("start", self.container_name(sandbox_id))
            status_obj = await self.get_status(sandbox_id)
            if not status_obj.ready:
                await self._wait_until_runtime_ready(sandbox_id)
                status_obj = await self.get_status(sandbox_id)
            return status_obj

        image = settings.agentbox_runtime_image
        self._workspace_path(sandbox_id)
        workspace_mount_path = self._workspace_mount_path(sandbox_id)
        workspace_mount = f"{workspace_mount_path}:/workspace"
        if self._selinux_enabled():
            workspace_mount += ":z"
        run_args = [
            "run",
            "-d",
            "--name",
            self.container_name(sandbox_id),
            "--label",
            "app.kubernetes.io/name=agentbox-sandbox",
            "--label",
            f"agentbox.work/sandbox-id={sandbox_id}",
            "--label",
            f"agentbox.work/provider={self.provider_name}",
            "-v",
            workspace_mount,
        ]
        if self._e2e_label_config():
            run_args.extend(["--label", "lemma.e2e=true"])
        if self._network_config():
            run_args.extend(["--network", self._network_config() or ""])
        else:
            for app in SANDBOX_APPS.values():
                run_args.extend(["-p", f"127.0.0.1::{app.port}"])
        if self._add_host_gateway_config():
            run_args.extend(["--add-host", "host.docker.internal:host-gateway"])
        for name, value in sorted(request_obj.env.items()):
            run_args.extend(["-e", f"{name}={value}"])
        if self._platform_config():
            run_args.extend(["--platform", self._platform_config() or ""])
        if self._memory_limit_config():
            run_args.extend(["--memory", self._memory_limit_config() or ""])
        if self._cpu_limit_config():
            run_args.extend(["--cpus", self._cpu_limit_config() or ""])
        run_args.append(image)

        await self._run_docker(*run_args)
        await self._wait_until_runtime_ready(sandbox_id)
        return await self.get_status(sandbox_id)

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        inspect_data = await self._inspect_raw(sandbox_id)
        if inspect_data is None:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        return self._status_from_inspect(sandbox_id, inspect_data)

    async def delete(self, sandbox_id: str) -> bool:
        validate_sandbox_id(sandbox_id)
        try:
            await self._run_docker("rm", "-f", self.container_name(sandbox_id))
            return True
        except RuntimeError:
            return False

    async def purge_storage(self, sandbox_id: str) -> bool:
        """Permanently remove a sandbox workspace after compute is gone.

        This is deliberately separate from ``delete`` because the lifecycle
        manager also replaces compute for durable environment changes. Those
        replacements must preserve user files; explicit DELETE and retention
        expiry invoke this hook only after provider compute has been removed.
        """

        validated_id = validate_sandbox_id(sandbox_id)

        def purge_locally() -> bool | None:
            root = self.storage_root.resolve()
            path = root / validated_id
            if path.parent != root:
                raise RuntimeError("Sandbox workspace escaped the storage root")
            try:
                if path.is_symlink():
                    path.unlink()
                    return True
                if path.is_dir():
                    shutil.rmtree(path)
                    return True
                if path.exists():
                    path.unlink()
                    return True
                return False
            except PermissionError:
                # Runtime files use UID/GID 10001. A non-root Linux manager
                # cannot remove their nested directories even though it owns
                # the configured storage root. Fall through to a daemon-side
                # cleanup container instead of weakening runtime ownership.
                return None

        local_result = await run_sync(purge_locally)
        if local_result is not None:
            return local_result

        host_root = self.storage_host_root.expanduser().resolve()
        workspace_mount = f"{host_root}:/agentbox-storage"
        if self._selinux_enabled():
            workspace_mount += ":z"
        await self._run_docker(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "/bin/rm",
            "-v",
            workspace_mount,
            settings.agentbox_runtime_image,
            "-rf",
            "--",
            f"/agentbox-storage/{validated_id}",
        )

        def verify_purged() -> bool:
            path = self.storage_root.resolve() / validated_id
            if path.exists() or path.is_symlink():
                raise RuntimeError(
                    "Provider cleanup completed but sandbox workspace remains"
                )
            return True

        return await run_sync(verify_purged)

    async def release(self, sandbox_id: str) -> bool:
        """Stop compute but retain the container and its workspace mount."""

        validate_sandbox_id(sandbox_id)
        existing = await self._inspect_sandbox(sandbox_id)
        if existing is None or not existing.ready:
            return False
        try:
            await self._run_docker("stop", self.container_name(sandbox_id))
            return True
        except RuntimeError:
            return False

    async def list_managed(self) -> list[ManagedSandbox]:
        output = await self._run_docker(
            "ps",
            "-a",
            "--filter",
            "label=app.kubernetes.io/name=agentbox-sandbox",
            "--format",
            "{{.Names}}",
        )
        managed: list[ManagedSandbox] = []
        for container_name in output.splitlines():
            if not container_name.startswith("agentbox-"):
                continue
            sandbox_id = container_name.removeprefix("agentbox-")
            inspect_data = await self._inspect_raw(sandbox_id)
            if inspect_data is None:
                continue
            config = inspect_data.get("Config")
            config_data = config if isinstance(config, dict) else {}
            labels = config_data.get("Labels")
            label_data = labels if isinstance(labels, dict) else {}
            labeled_id = label_data.get("agentbox.work/sandbox-id")
            if not isinstance(labeled_id, str) or labeled_id != sandbox_id:
                continue
            provider_id = inspect_data.get("Id")
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(
                        sandbox_id=sandbox_id,
                        provider_id=(
                            provider_id
                            if isinstance(provider_id, str)
                            else container_name
                        ),
                    ),
                    status=self._status_from_inspect(sandbox_id, inspect_data),
                    instance_id=(
                        provider_id if isinstance(provider_id, str) else container_name
                    ),
                    metadata={
                        str(key): str(value) for key, value in label_data.items()
                    },
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
        inspect_data = await self._inspect_raw(sandbox_id)
        if inspect_data is None:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        status = self._status_from_inspect(sandbox_id, inspect_data)
        if not status.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        app_status = status.apps.get(app.name)
        base_url = app_status.private_url if app_status else None
        if app.name == "runtime" and not base_url:
            base_url = status.runtime_url
        if not base_url:
            raise HTTPException(
                status_code=409, detail="Sandbox app endpoint is missing"
            )
        provider_id = inspect_data.get("Id")
        return SandboxEndpoint(
            base_url=base_url,
            instance_id=(
                str(provider_id) if provider_id else self.container_name(sandbox_id)
            ),
        )

    async def _inspect_sandbox(self, sandbox_id: str) -> SandboxInternalStatus | None:
        inspect_data = await self._inspect_raw(sandbox_id)
        if inspect_data is None:
            return None
        return self._status_from_inspect(sandbox_id, inspect_data)

    async def _get_status_or_none(
        self, sandbox_id: str
    ) -> SandboxInternalStatus | None:
        try:
            return await self.get_status(sandbox_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            raise

    async def _inspect_raw(self, sandbox_id: str) -> dict[str, object] | None:
        validate_sandbox_id(sandbox_id)
        try:
            output = await self._run_docker("inspect", self.container_name(sandbox_id))
        except RuntimeError:
            return None
        parsed = json.loads(output)
        if not isinstance(parsed, list) or not parsed:
            return None
        item = parsed[0]
        return item if isinstance(item, dict) else None

    async def _run_docker(self, *args: str) -> str:
        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.cli_name, *args],
                check=False,
                capture_output=True,
                text=True,
            )

        proc = await run_sync(_run)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            raise RuntimeError(
                f"{self.cli_name} command failed: {self.cli_name} {' '.join(args)} :: {stderr}"
            )
        return proc.stdout.strip()

    async def _wait_until_runtime_ready(self, sandbox_id: str) -> None:
        deadline = time.monotonic() + settings.agentbox_sandbox_ready_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            status_obj = await self._inspect_sandbox(sandbox_id)
            if status_obj is not None:
                try:
                    if await run_sync(self._check_eager_apps_health, status_obj):
                        return
                except (OSError, error.URLError) as exc:
                    last_error = exc
            await asyncio.sleep(0.25)
        detail = f": {last_error}" if last_error else ""
        raise HTTPException(
            status_code=504,
            detail=f"Sandbox did not become ready before timeout{detail}",
        )

    def _check_eager_apps_health(self, status_obj: SandboxInternalStatus) -> bool:
        if not status_obj.ready:
            return False
        for app_spec in SANDBOX_APPS.values():
            if app_spec.startup != "eager":
                continue
            app_status = status_obj.apps.get(app_spec.name)
            if app_status is None or not app_status.private_url:
                return False
            if not self._check_health(app_status.private_url, app_spec.health_path):
                return False
        return True

    def _check_health(self, base_url: str, health_path: str = "/health") -> bool:
        path = health_path if health_path.startswith("/") else f"/{health_path}"
        req = request.Request(f"{base_url.rstrip('/')}{path}", method="GET")
        with request.urlopen(req, timeout=2) as response:
            return 200 <= response.status < 300

    def _status_from_inspect(
        self,
        sandbox_id: str,
        inspect_data: dict[str, object],
    ) -> SandboxInternalStatus:
        state = inspect_data.get("State")
        state_data = state if isinstance(state, dict) else {}
        network_settings = inspect_data.get("NetworkSettings")
        network_data = network_settings if isinstance(network_settings, dict) else {}

        running = bool(state_data.get("Running"))
        status_text = str(state_data.get("Status") or "")
        lifecycle = self._lifecycle_status(running, status_text)

        if self._network_config():
            # Network mode: no published ports; the manager reaches the sandbox
            # by container-name DNS on the shared network.
            dns_name = self.container_name(sandbox_id)
            return SandboxInternalStatus(
                id=sandbox_id,
                status=lifecycle,
                ready=running,
                pod_ip=dns_name if running else None,
                runtime_url=(
                    f"http://{dns_name}:{settings.agentbox_runtime_port}"
                    if running
                    else None
                ),
                apps=self._app_statuses_from_network(dns_name, running),
            )

        ports = network_data.get("Ports")
        port_data = ports if isinstance(ports, dict) else {}
        runtime_port = self._mapped_host_port(port_data, settings.agentbox_runtime_port)
        apps = self._app_statuses_from_ports(port_data)

        return SandboxInternalStatus(
            id=sandbox_id,
            status=lifecycle,
            ready=running and runtime_port is not None,
            pod_ip=self._endpoint_host() if runtime_port else None,
            runtime_url=self._runtime_url(runtime_port) if runtime_port else None,
            apps=apps,
        )

    def _app_statuses_from_network(
        self,
        dns_name: str,
        running: bool,
    ) -> dict[str, SandboxInternalAppStatus]:
        return {
            app.name: SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=running,
                private_url=f"http://{dns_name}:{app.port}" if running else None,
            )
            for app in SANDBOX_APPS.values()
        }

    def _app_statuses_from_ports(
        self,
        ports: dict[object, object],
    ) -> dict[str, SandboxInternalAppStatus]:
        statuses: dict[str, SandboxInternalAppStatus] = {}
        for app in SANDBOX_APPS.values():
            host_port = self._mapped_host_port(ports, app.port)
            statuses[app.name] = SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=host_port is not None,
                private_url=self._runtime_url(host_port) if host_port else None,
            )
        return statuses

    def _runtime_base_url(self, status_obj: SandboxInternalStatus | None) -> str | None:
        if status_obj is None:
            return None
        return status_obj.runtime_url

    def _lifecycle_status(self, running: bool, status_text: str) -> str:
        if running:
            return "RUNNING"
        normalized = status_text.lower()
        if normalized in {"created", "restarting"}:
            return "CREATING"
        if normalized in {"exited", "removing"}:
            return "STOPPED"
        return "ERROR"

    def _runtime_url(self, host_port: str) -> str:
        return f"http://{self._endpoint_host()}:{host_port}"

    def _mapped_host_port(
        self,
        ports: dict[object, object],
        container_port: int,
    ) -> str | None:
        bindings = ports.get(f"{container_port}/tcp")
        if not isinstance(bindings, list) or not bindings:
            return None
        first = bindings[0]
        if not isinstance(first, dict):
            return None
        host_port = first.get("HostPort")
        return str(host_port) if host_port else None

    def _workspace_path(self, sandbox_id: str) -> Path:
        path = self.storage_root / validate_sandbox_id(sandbox_id)
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)
        return path.resolve()

    def _workspace_mount_path(self, sandbox_id: str) -> Path:
        path = self.storage_host_root / validate_sandbox_id(sandbox_id)
        return path.expanduser().resolve()
