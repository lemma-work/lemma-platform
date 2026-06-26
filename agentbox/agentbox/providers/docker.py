from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib import error, request

from fastapi import HTTPException

from agentbox.apps import SANDBOX_APPS
from agentbox.config import settings
from agentbox.runtime_proxy import RuntimeProxy
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
    WriteStdinRequest,
)
from agentbox.to_thread import run_sync


def docker_container_name(sandbox_id: str) -> str:
    return f"agentbox-{validate_sandbox_id(sandbox_id)}"


class DockerSandboxProvider:
    cli_name = "docker"
    namespace = "docker"

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

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        stdout, stderr, result, error_name, exit_code = await proxy.execute_code(
            code,
            timeout_seconds,
            session_id=session_id,
        )
        return ExecutePythonResponse(
            sandbox_id=sandbox_id,
            session_id=session_id,
            stdout=stdout,
            stderr=stderr,
            result=result,
            error_name=error_name,
            exit_code=exit_code,
            status="completed" if exit_code == 0 else "error",
        )

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.create_session(session_id, request_obj)

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.delete_session(session_id)

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.exec_session_process_command(session_id, request_obj)

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.write_session_process_stdin(session_id, request_obj)

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.terminate_session_process(session_id, process_id)

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        return await proxy.list_session_processes(session_id)

    async def _runtime_proxy(self, sandbox_id: str) -> RuntimeProxy:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        runtime_url = self._runtime_base_url(status_obj)
        if runtime_url is None:
            raise HTTPException(status_code=409, detail="Sandbox runtime endpoint is missing")
        return RuntimeProxy(runtime_url, sandbox_id)

    async def _inspect_sandbox(self, sandbox_id: str) -> SandboxInternalStatus | None:
        inspect_data = await self._inspect_raw(sandbox_id)
        if inspect_data is None:
            return None
        return self._status_from_inspect(sandbox_id, inspect_data)

    async def _get_status_or_none(self, sandbox_id: str) -> SandboxInternalStatus | None:
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
                    f"http://{dns_name}:{settings.agentbox_runtime_port}" if running else None
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
