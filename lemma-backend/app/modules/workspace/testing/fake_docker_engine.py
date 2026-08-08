"""An in-memory stand-in for the Docker Engine REST client.

Models the parts of Docker the provider actually depends on: names are unique,
creating a taken name fails, and label filters are conjunctive. Those three
behaviours are what the provider's idempotence and adoption logic rest on, so
a fake that got them wrong would prove nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.modules.workspace.providers.docker_engine import (
    DockerContainerCreateRequest,
    DockerContainerCreateResponse,
    DockerContainerSummary,
    DockerEngineError,
    DockerVolume,
    DockerVolumeCreateRequest,
)


@dataclass
class FakeContainer:
    container_id: str
    name: str
    image: str
    labels: dict[str, str]
    running: bool = False
    status: str = "created"
    exit_code: int = 0


@dataclass
class FakeDockerEngine:
    containers: dict[str, FakeContainer] = field(default_factory=dict)
    volumes: dict[str, DockerVolume] = field(default_factory=dict)
    create_calls: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    archives: list[tuple[str, str]] = field(default_factory=list)
    # Set to raise from the next create, to exercise recovery paths.
    fail_next_create: Exception | None = None
    _next_id: int = 0

    # -- volumes ------------------------------------------------------

    async def inspect_volume(
        self, name: str, *, deadline_at: datetime
    ) -> DockerVolume | None:
        return self.volumes.get(name)

    async def create_volume(
        self, request: DockerVolumeCreateRequest, *, deadline_at: datetime
    ) -> DockerVolume:
        volume = DockerVolume.model_validate(
            {"Name": request.name, "Labels": dict(request.labels)}
        )
        self.volumes[request.name] = volume
        return volume

    async def list_volumes(
        self, *, labels: dict[str, str], deadline_at: datetime
    ) -> tuple[DockerVolume, ...]:
        return tuple(
            volume
            for volume in self.volumes.values()
            if all(volume.labels.get(k) == v for k, v in labels.items())
        )

    async def delete_volume(self, name: str, *, deadline_at: datetime) -> bool:
        return self.volumes.pop(name, None) is not None

    # -- containers ---------------------------------------------------

    async def create_container(
        self,
        name: str,
        request: DockerContainerCreateRequest,
        *,
        deadline_at: datetime,
    ) -> DockerContainerCreateResponse:
        self.create_calls.append(name)
        if self.fail_next_create is not None:
            error, self.fail_next_create = self.fail_next_create, None
            raise error
        if name in self.containers:
            raise DockerEngineError(f"Conflict. The container name {name} is in use")
        self._next_id += 1
        container_id = f"container-{self._next_id}"
        self.containers[name] = FakeContainer(
            container_id=container_id,
            name=name,
            image=request.image,
            labels=dict(request.labels),
        )
        return DockerContainerCreateResponse.model_validate(
            {"Id": container_id, "Warnings": []}
        )

    async def inspect_container(self, ref: str, *, deadline_at: datetime) -> Any:
        container = self._find(ref)
        if container is None:
            return None
        return _inspect_payload(container)

    async def list_containers(
        self, *, labels: dict[str, str], deadline_at: datetime
    ) -> tuple[DockerContainerSummary, ...]:
        return tuple(
            DockerContainerSummary.model_validate(
                {
                    "Id": container.container_id,
                    "Image": container.image,
                    "State": "running" if container.running else "exited",
                    "Labels": dict(container.labels),
                    "Names": [f"/{container.name}"],
                }
            )
            for container in self.containers.values()
            if all(container.labels.get(k) == v for k, v in labels.items())
        )

    async def start_container(self, ref: str, *, deadline_at: datetime) -> None:
        container = self._find(ref)
        if container is None:
            raise DockerEngineError("no such container")
        container.running = True
        container.status = "running"

    async def stop_container(
        self, ref: str, *, deadline_at: datetime, grace_seconds: int = 5
    ) -> None:
        container = self._find(ref)
        if container is None:
            raise DockerEngineError("no such container")
        container.running = False
        container.status = "exited"
        self.stopped.append(container.name)

    async def delete_container(
        self, ref: str, *, deadline_at: datetime, force: bool = False
    ) -> None:
        container = self._find(ref)
        if container is None:
            return
        self.destroyed.append(container.name)
        self.containers.pop(container.name, None)

    async def put_archive(
        self, ref: str, path: str, payload: bytes, *, deadline_at: datetime
    ) -> None:
        container = self._find(ref)
        if container is None:
            raise DockerEngineError("no such container")
        self.archives.append((container.name, path))

    async def close(self) -> None:
        return None

    def _find(self, ref: str) -> FakeContainer | None:
        if ref in self.containers:
            return self.containers[ref]
        for container in self.containers.values():
            if container.container_id == ref:
                return container
        return None


def _inspect_payload(container: FakeContainer) -> Any:
    from app.modules.workspace.providers.docker_engine import DockerContainerInspect

    return DockerContainerInspect.model_validate(
        {
            "Id": container.container_id,
            "Name": f"/{container.name}",
            "State": {
                "Status": container.status,
                "Running": container.running,
                "ExitCode": container.exit_code,
            },
            "Config": {"Image": container.image, "Labels": dict(container.labels)},
            "NetworkSettings": {
                "Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "34567"}]},
                "Networks": {},
            },
        }
    )
