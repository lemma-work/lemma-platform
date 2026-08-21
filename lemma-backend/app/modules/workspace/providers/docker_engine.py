from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field


class DockerApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)


class DockerEmptyObject(DockerApiModel):
    pass


class DockerPortBinding(DockerApiModel):
    host_ip: str = Field(alias="HostIp")
    host_port: str = Field(alias="HostPort")


class DockerHostConfig(DockerApiModel):
    binds: tuple[str, ...] = Field(default=(), alias="Binds")
    port_bindings: dict[str, tuple[DockerPortBinding, ...]] = Field(
        default_factory=dict, alias="PortBindings"
    )
    cap_drop: tuple[str, ...] = Field(default=("ALL",), alias="CapDrop")
    security_opt: tuple[str, ...] = Field(
        default=("no-new-privileges:true",), alias="SecurityOpt"
    )
    pids_limit: int = Field(default=512, alias="PidsLimit")
    memory: int = Field(default=0, alias="Memory")
    nano_cpus: int = Field(default=0, alias="NanoCpus")
    readonly_rootfs: bool = Field(default=False, alias="ReadonlyRootfs")
    tmpfs: dict[str, str] = Field(default_factory=dict, alias="Tmpfs")
    extra_hosts: tuple[str, ...] = Field(default=(), alias="ExtraHosts")
    network_mode: str | None = Field(default=None, alias="NetworkMode")


class DockerContainerCreateRequest(DockerApiModel):
    image: str = Field(alias="Image")
    command: tuple[str, ...] | None = Field(default=None, alias="Cmd")
    labels: dict[str, str] = Field(alias="Labels")
    user: str | None = Field(default=None, alias="User")
    working_dir: str = Field(default="/workspace", alias="WorkingDir")
    env: tuple[str, ...] = Field(default=(), alias="Env")
    exposed_ports: dict[str, DockerEmptyObject] = Field(
        default_factory=dict, alias="ExposedPorts"
    )
    host_config: DockerHostConfig = Field(alias="HostConfig")
    tty: bool = Field(default=False, alias="Tty")
    open_stdin: bool = Field(default=False, alias="OpenStdin")


class DockerContainerCreateResponse(DockerApiModel):
    container_id: str = Field(alias="Id")
    warnings: tuple[str, ...] = Field(default=(), alias="Warnings")


class DockerVolumeCreateRequest(DockerApiModel):
    name: str = Field(alias="Name")
    labels: dict[str, str] = Field(alias="Labels")


class DockerVolume(DockerApiModel):
    name: str = Field(alias="Name")
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")


class DockerNetworkCreateRequest(DockerApiModel):
    name: str = Field(alias="Name")
    check_duplicate: bool = Field(default=True, alias="CheckDuplicate")
    driver: str = Field(default="bridge", alias="Driver")
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")


class DockerNetworkCreateResponse(DockerApiModel):
    network_id: str = Field(alias="Id")
    warning: str = Field(default="", alias="Warning")


class DockerContainerState(DockerApiModel):
    status: str = Field(alias="Status")
    running: bool = Field(alias="Running")
    exit_code: int = Field(default=0, alias="ExitCode")
    error: str = Field(default="", alias="Error")


class DockerContainerConfig(DockerApiModel):
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")
    image: str = Field(alias="Image")


class DockerPublishedPort(DockerApiModel):
    host_ip: str = Field(alias="HostIp")
    host_port: str = Field(alias="HostPort")


class DockerNetworkAttachment(DockerApiModel):
    ip_address: str = Field(default="", alias="IPAddress")


class DockerNetworkSettings(DockerApiModel):
    ports: dict[str, tuple[DockerPublishedPort, ...] | None] = Field(
        default_factory=dict, alias="Ports"
    )
    networks: dict[str, DockerNetworkAttachment] = Field(
        default_factory=dict, alias="Networks"
    )


class DockerContainerInspect(DockerApiModel):
    container_id: str = Field(alias="Id")
    state: DockerContainerState = Field(alias="State")
    config: DockerContainerConfig = Field(alias="Config")
    host_config: DockerHostConfig = Field(
        default_factory=DockerHostConfig,
        alias="HostConfig",
    )
    network_settings: DockerNetworkSettings = Field(alias="NetworkSettings")


class DockerContainerSummary(DockerApiModel):
    container_id: str = Field(alias="Id")
    image: str = Field(alias="Image")
    state: str = Field(alias="State")
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")
    # Docker reports these with a leading slash. The sweep parses identity out
    # of the name, so it has to be carried here and not just inferred from
    # labels -- a pre-cutover container has the old labels but is still ours.
    names: tuple[str, ...] = Field(default=(), alias="Names")


class DockerExecCreateRequest(DockerApiModel):
    argv: tuple[str, ...] = Field(alias="Cmd")
    attach_stdin: bool = Field(default=False, alias="AttachStdin")
    attach_stdout: bool = Field(default=True, alias="AttachStdout")
    attach_stderr: bool = Field(default=True, alias="AttachStderr")
    tty: bool = Field(default=False, alias="Tty")
    working_dir: str = Field(default="/workspace", alias="WorkingDir")
    env: tuple[str, ...] = Field(default=(), alias="Env")


class DockerExecCreateResponse(DockerApiModel):
    exec_id: str = Field(alias="Id")


class DockerExecStartRequest(DockerApiModel):
    detach: bool = Field(default=False, alias="Detach")
    tty: bool = Field(default=False, alias="Tty")


class DockerExecInspect(DockerApiModel):
    exec_id: str = Field(alias="ID")
    running: bool = Field(alias="Running")
    exit_code: int | None = Field(alias="ExitCode")


class DockerErrorResponse(DockerApiModel):
    message: str


class DockerEngineError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DockerRequestAmbiguous(DockerEngineError):
    pass


@dataclass(frozen=True, slots=True)
class DockerExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class DockerEngineClient:
    """Small typed async Docker Engine API client; it never shells out to Docker."""

    def __init__(
        self,
        *,
        api_version: str = "v1.44",
        socket_path: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if transport is None:
            if socket_path is None:
                raise ValueError("Docker socket_path is required without a transport")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
        self._api_prefix = f"/{api_version.strip('/')}"
        self._request_timeout_seconds = request_timeout_seconds
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://docker-engine",
            timeout=None,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def inspect_volume(
        self, name: str, *, deadline_at: datetime
    ) -> DockerVolume | None:
        response = await self._request(
            "GET", f"/volumes/{name}", deadline_at=deadline_at, expected=(200, 404)
        )
        if response.status_code == 404:
            return None
        return DockerVolume.model_validate(response.json())

    async def list_volumes(
        self,
        *,
        labels: dict[str, str],
        deadline_at: datetime,
    ) -> tuple[DockerVolume, ...]:
        """Find volumes by label.

        This is how a sandbox adopts the volume it owned before the workspace
        module took over provisioning: the name embeds a random token minted in
        a database that is going away, but the labels still carry the logical
        id, so the volume can be found even though its name cannot be derived.
        """
        filters = {"label": [f"{name}={value}" for name, value in labels.items()]}
        response = await self._request(
            "GET",
            "/volumes",
            deadline_at=deadline_at,
            expected=(200,),
            params={"filters": json.dumps(filters, sort_keys=True)},
        )
        # Docker returns {"Volumes": null} rather than an empty list when
        # nothing matches, so a plain `or ()` is doing real work here.
        return tuple(
            DockerVolume.model_validate(item)
            for item in (response.json().get("Volumes") or ())
        )

    async def create_volume(
        self, request: DockerVolumeCreateRequest, *, deadline_at: datetime
    ) -> DockerVolume:
        response = await self._request(
            "POST",
            "/volumes/create",
            deadline_at=deadline_at,
            expected=(201,),
            json_body=request,
        )
        return DockerVolume.model_validate(response.json())

    async def create_container(
        self,
        name: str,
        request: DockerContainerCreateRequest,
        *,
        deadline_at: datetime,
    ) -> DockerContainerCreateResponse:
        response = await self._request(
            "POST",
            "/containers/create",
            deadline_at=deadline_at,
            expected=(201,),
            params={"name": name},
            json_body=request,
            ambiguous_on_transport=True,
        )
        return DockerContainerCreateResponse.model_validate(response.json())

    async def inspect_container(
        self, container_id: str, *, deadline_at: datetime
    ) -> DockerContainerInspect | None:
        response = await self._request(
            "GET",
            f"/containers/{container_id}/json",
            deadline_at=deadline_at,
            expected=(200, 404),
        )
        if response.status_code == 404:
            return None
        return DockerContainerInspect.model_validate(response.json())

    async def list_containers(
        self,
        *,
        labels: dict[str, str],
        deadline_at: datetime,
    ) -> tuple[DockerContainerSummary, ...]:
        filters = {"label": [f"{name}={value}" for name, value in labels.items()]}
        response = await self._request(
            "GET",
            "/containers/json",
            deadline_at=deadline_at,
            expected=(200,),
            params={"all": "true", "filters": json.dumps(filters, sort_keys=True)},
        )
        return tuple(
            DockerContainerSummary.model_validate(item) for item in response.json()
        )

    async def start_container(
        self, container_id: str, *, deadline_at: datetime
    ) -> None:
        await self._request(
            "POST",
            f"/containers/{container_id}/start",
            deadline_at=deadline_at,
            expected=(204, 304),
        )

    async def stop_container(
        self,
        container_id: str,
        *,
        deadline_at: datetime,
        grace_seconds: int = 5,
    ) -> bool:
        response = await self._request(
            "POST",
            f"/containers/{container_id}/stop",
            deadline_at=deadline_at,
            expected=(204, 304, 404),
            params={"t": str(grace_seconds)},
        )
        return response.status_code == 204

    async def delete_container(
        self,
        container_id: str,
        *,
        deadline_at: datetime,
        force: bool = True,
    ) -> bool:
        response = await self._request(
            "DELETE",
            f"/containers/{container_id}",
            deadline_at=deadline_at,
            expected=(204, 404),
            params={"force": "true" if force else "false", "v": "false"},
        )
        return response.status_code == 204

    async def delete_volume(self, name: str, *, deadline_at: datetime) -> bool:
        response = await self._request(
            "DELETE",
            f"/volumes/{name}",
            deadline_at=deadline_at,
            expected=(204, 404),
        )
        return response.status_code == 204

    async def create_network(
        self,
        request: DockerNetworkCreateRequest,
        *,
        deadline_at: datetime,
    ) -> DockerNetworkCreateResponse:
        response = await self._request(
            "POST",
            "/networks/create",
            deadline_at=deadline_at,
            expected=(201,),
            json_body=request,
        )
        return DockerNetworkCreateResponse.model_validate(response.json())

    async def delete_network(self, network_id: str, *, deadline_at: datetime) -> bool:
        response = await self._request(
            "DELETE",
            f"/networks/{network_id}",
            deadline_at=deadline_at,
            expected=(204, 404),
        )
        return response.status_code == 204

    async def put_archive(
        self,
        container_id: str,
        destination: str,
        archive: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        await self._request(
            "PUT",
            f"/containers/{container_id}/archive",
            deadline_at=deadline_at,
            expected=(200,),
            params={"path": destination},
            content=archive,
            content_type="application/x-tar",
        )

    async def get_archive(
        self,
        container_id: str,
        path: str,
        *,
        deadline_at: datetime,
    ) -> bytes:
        response = await self._request(
            "GET",
            f"/containers/{container_id}/archive",
            deadline_at=deadline_at,
            expected=(200,),
            params={"path": path},
        )
        return response.content

    async def create_exec(
        self,
        container_id: str,
        request: DockerExecCreateRequest,
        *,
        deadline_at: datetime,
    ) -> DockerExecCreateResponse:
        response = await self._request(
            "POST",
            f"/containers/{container_id}/exec",
            deadline_at=deadline_at,
            expected=(201,),
            json_body=request,
        )
        return DockerExecCreateResponse.model_validate(response.json())

    async def start_exec(
        self,
        exec_id: str,
        request: DockerExecStartRequest,
        *,
        deadline_at: datetime,
    ) -> bytes:
        response = await self._request(
            "POST",
            f"/exec/{exec_id}/start",
            deadline_at=deadline_at,
            expected=(200,),
            json_body=request,
            ambiguous_on_transport=True,
        )
        return response.content

    async def inspect_exec(
        self, exec_id: str, *, deadline_at: datetime
    ) -> DockerExecInspect:
        response = await self._request(
            "GET",
            f"/exec/{exec_id}/json",
            deadline_at=deadline_at,
            expected=(200,),
        )
        return DockerExecInspect.model_validate(response.json())

    async def run_exec(
        self,
        container_id: str,
        argv: tuple[str, ...],
        *,
        working_dir: str = "/workspace",
        deadline_at: datetime,
    ) -> int:
        created = await self.create_exec(
            container_id,
            DockerExecCreateRequest(argv=argv, working_dir=working_dir),
            deadline_at=deadline_at,
        )
        await self.start_exec(
            created.exec_id,
            DockerExecStartRequest(),
            deadline_at=deadline_at,
        )
        while True:
            inspected = await self.inspect_exec(
                created.exec_id, deadline_at=deadline_at
            )
            if not inspected.running:
                if inspected.exit_code is None:
                    raise DockerEngineError("Docker exec stopped without an exit code")
                return inspected.exit_code
            if datetime.now(timezone.utc) >= deadline_at:
                raise DockerEngineError("Docker exec did not finish before deadline")
            await asyncio.sleep(0.02)

    async def run_exec_capture(
        self,
        container_id: str,
        argv: tuple[str, ...],
        *,
        environment: tuple[str, ...] = (),
        working_dir: str = "/tmp",
        deadline_at: datetime,
    ) -> DockerExecResult:
        created = await self.create_exec(
            container_id,
            DockerExecCreateRequest(
                argv=argv,
                env=environment,
                working_dir=working_dir,
            ),
            deadline_at=deadline_at,
        )
        output = await self.start_exec(
            created.exec_id,
            DockerExecStartRequest(),
            deadline_at=deadline_at,
        )
        inspected = await self.inspect_exec(created.exec_id, deadline_at=deadline_at)
        if inspected.exit_code is None:
            raise DockerEngineError("Docker exec completed without an exit code")
        stdout, stderr = self._decode_multiplexed_stream(output)
        return DockerExecResult(
            exit_code=inspected.exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _decode_multiplexed_stream(payload: bytes) -> tuple[bytes, bytes]:
        stdout = bytearray()
        stderr = bytearray()
        offset = 0
        while offset < len(payload):
            if len(payload) - offset < 8:
                raise DockerEngineError("Docker exec returned a truncated stream frame")
            channel = payload[offset]
            length = int.from_bytes(payload[offset + 4 : offset + 8], "big")
            offset += 8
            if len(payload) - offset < length:
                raise DockerEngineError(
                    "Docker exec returned a truncated stream payload"
                )
            data = payload[offset : offset + length]
            offset += length
            if channel == 1:
                stdout.extend(data)
            elif channel == 2:
                stderr.extend(data)
        return bytes(stdout), bytes(stderr)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        deadline_at: datetime,
        expected: tuple[int, ...],
        params: dict[str, str] | None = None,
        json_body: DockerApiModel | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        ambiguous_on_transport: bool = False,
    ) -> httpx.Response:
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise DockerEngineError("Docker operation deadline has elapsed")
        timeout = min(remaining, self._request_timeout_seconds)
        body: dict[str, Any] | None = None
        if json_body is not None:
            body = json_body.model_dump(mode="json", by_alias=True, exclude_none=True)
        headers = {"Content-Type": content_type} if content_type is not None else None
        try:
            response = await self._client.request(
                method,
                f"{self._api_prefix}{path}",
                params=params,
                json=body,
                content=content,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TransportError as exc:
            error_type = (
                DockerRequestAmbiguous if ambiguous_on_transport else DockerEngineError
            )
            raise error_type(
                f"Docker Engine transport failed: {type(exc).__name__}"
            ) from exc
        if response.status_code not in expected:
            try:
                message = DockerErrorResponse.model_validate(response.json()).message
            except ValueError, TypeError:
                message = f"Docker Engine returned HTTP {response.status_code}"
            raise DockerEngineError(message, status_code=response.status_code)
        return response
