"""Declarative service specs for the installed stack, rendered to `run` args.

This replaces compose: one codepath for docker and podman, with explicit
start ordering and health gating handled by lifecycle.py. Only frontend,
backend, and agentbox publish host ports (loopback only); infra is reachable
solely over the lemma-local-net network.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path

from tomlkit import TOMLDocument

from lemma_stack.config import render, store
from lemma_stack.paths import LocalPaths
from lemma_stack.release.manifest import ReleaseManifest

NETWORK_NAME = render.NETWORK_NAME
CONTAINER_PREFIX = render.CONTAINER_PREFIX
POSTGRES_VOLUME = render.POSTGRES_VOLUME

STACK_LABEL = "work.lemma.stack"
SERVICE_LABEL = "work.lemma.service"
HASH_LABEL = "work.lemma.config-hash"

PODMAN_SOCKET_MOUNT = "/run/podman/podman.sock"
DOCKER_SOCKET_MOUNT = "/var/run/docker.sock"


@dataclass(frozen=True)
class HealthCheck:
    cmd: str
    interval: str = "5s"
    timeout: str = "5s"
    retries: int = 30


@dataclass(frozen=True)
class ServiceSpec:
    name: str  # short name; doubles as the DNS alias on the network
    image: str
    env: dict[str, str] = field(default_factory=dict)
    ports: tuple[tuple[int, int], ...] = ()  # (host, container), bound to 127.0.0.1
    binds: tuple[tuple[str, str, str], ...] = ()  # (source, target, opts) — opts "" | "ro"
    volumes: tuple[tuple[str, str], ...] = ()  # (named volume, target)
    command: tuple[str, ...] = ()
    user: str | None = None
    security_opts: tuple[str, ...] = ()
    health: HealthCheck | None = None
    wait_healthy: bool = False  # gate the start sequence on container health
    wait_http: str | None = None  # gate on an HTTP 200 from the host

    @property
    def container_name(self) -> str:
        return f"{CONTAINER_PREFIX}-{self.name}"

    def config_hash(self) -> str:
        payload = json.dumps(
            {
                "image": self.image,
                "env": self.env,
                "ports": self.ports,
                "binds": self.binds,
                "volumes": self.volumes,
                "command": self.command,
                "user": self.user,
                "security_opts": self.security_opts,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def selinux_enforcing() -> bool:
    """Return whether the host Linux kernel reports SELinux support."""
    return Path("/sys/fs/selinux/enforce").exists()


def agentbox_socket_requires_label_disable(provider: str) -> bool:
    """Return whether the nested runtime socket needs an unconfined label.

    Podman on macOS and Windows runs containers inside an SELinux-enforcing
    Fedora CoreOS VM, so checking only the host filesystem misses that case.
    """
    if selinux_enforcing():
        return True
    return provider == "podman" and platform.system() in {"Darwin", "Windows"}


def run_args(spec: ServiceSpec, *, selinux: bool | None = None) -> list[str]:
    """Render the full `<runtime> run` argument list for a spec (pure, testable)."""
    if selinux is None:
        selinux = selinux_enforcing()
    args = [
        "run",
        "-d",
        "--name",
        spec.container_name,
        "--label",
        f"{STACK_LABEL}=local",
        "--label",
        f"{SERVICE_LABEL}={spec.name}",
        "--label",
        f"{HASH_LABEL}={spec.config_hash()}",
        "--network",
        NETWORK_NAME,
        "--network-alias",
        spec.name,
        "--restart",
        "unless-stopped",
    ]
    for host_port, container_port in spec.ports:
        args.extend(["-p", f"127.0.0.1:{host_port}:{container_port}"])
    for key, value in spec.env.items():
        args.extend(["-e", f"{key}={value}"])
    for source, target, opts in spec.binds:
        flags = [opt for opt in (opts,) if opt]
        if selinux and "ro" not in flags and not source.endswith(".sock"):
            flags.append("z")
        suffix = f":{','.join(flags)}" if flags else ""
        args.extend(["-v", f"{source}:{target}{suffix}"])
    for volume, target in spec.volumes:
        args.extend(["-v", f"{volume}:{target}"])
    if spec.user:
        args.extend(["--user", spec.user])
    for opt in spec.security_opts:
        args.extend(["--security-opt", opt])
    if spec.health:
        args.extend(
            [
                "--health-cmd",
                spec.health.cmd,
                "--health-interval",
                spec.health.interval,
                "--health-timeout",
                spec.health.timeout,
                "--health-retries",
                str(spec.health.retries),
            ]
        )
    args.append(spec.image)
    args.extend(spec.command)
    return args


def build_specs(
    doc: TOMLDocument,
    paths: LocalPaths,
    manifest: ReleaseManifest,
    *,
    provider: str,
    host_socket: str,
) -> list[ServiceSpec]:
    """The installed stack, in start order."""
    kreuzberg_enabled = store.feature(doc, "kreuzberg")
    socket_mount = PODMAN_SOCKET_MOUNT if provider == "podman" else DOCKER_SOCKET_MOUNT

    specs = [
        ServiceSpec(
            name="db",
            image=manifest.infra_image("postgres"),
            env={
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "postgres",
                "POSTGRES_DB": "lemma",
            },
            volumes=((POSTGRES_VOLUME, "/var/lib/postgresql/data"),),
            binds=((str(paths.postgres_init_dir), "/docker-entrypoint-initdb.d", "ro"),),
            health=HealthCheck(cmd="pg_isready -U postgres -h localhost"),
            wait_healthy=True,
        ),
        ServiceSpec(
            name="redis",
            image=manifest.infra_image("redis"),
            health=HealthCheck(cmd="redis-cli ping"),
        ),
        ServiceSpec(
            name="supertokens",
            image=manifest.infra_image("supertokens"),
            env={
                "POSTGRESQL_CONNECTION_URI": "postgresql://postgres:postgres@db:5432/supertokens",
            },
            health=HealthCheck(
                cmd=(
                    "bash -c 'exec 3<>/dev/tcp/127.0.0.1/3567 && "
                    'echo -e "GET /hello HTTP/1.1\\r\\nhost: 127.0.0.1:3567\\r\\n'
                    'Connection: close\\r\\n\\r\\n" >&3 && cat <&3 | grep "Hello"\''
                ),
                interval="10s",
                retries=12,
            ),
            wait_healthy=True,
        ),
    ]

    if kreuzberg_enabled:
        specs.append(
            ServiceSpec(
                name="kreuzberg",
                image=manifest.infra_image("kreuzberg"),
                health=HealthCheck(
                    cmd="curl -f http://localhost:8000/health",
                    interval="30s",
                    timeout="10s",
                    retries=5,
                ),
            )
        )

    specs.append(
        ServiceSpec(
            name="agentbox",
            image=manifest.image("agentbox").pull_ref,
            env=render.agentbox_env(
                doc,
                paths,
                provider=provider,
                runtime_image=manifest.image("agentbox_runtime").pull_ref,
                container_socket=socket_mount,
            ),
            ports=((store.port(doc, "agentbox"), 8000),),
            binds=(
                (str(paths.state_dir), render.STATE_MOUNT, ""),
                (str(paths.workspaces_dir), render.WORKSPACES_MOUNT, ""),
                (host_socket, socket_mount, ""),
            ),
            user="root",
            # the mounted API socket must not be relabeled by SELinux
            security_opts=("label=disable",)
            if agentbox_socket_requires_label_disable(provider)
            else (),
        )
    )

    specs.append(
        ServiceSpec(
            name="backend",
            image=manifest.image("backend").pull_ref,
            env=render.backend_env(doc, paths),
            command=(
                "uvicorn",
                "standalone_app:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--ws",
                "websockets-sansio",
            ),
            ports=((store.port(doc, "backend"), 8000),),
            binds=(
                (str(paths.state_dir), render.STATE_MOUNT, ""),
                (str(paths.workspaces_dir), render.WORKSPACES_MOUNT, ""),
                (str(paths.object_storage_dir), render.OBJECT_STORAGE_MOUNT, ""),
            ),
            wait_http=f"{render.backend_origin(doc)}/health",
        )
    )

    specs.append(
        ServiceSpec(
            name="frontend",
            image=manifest.image("frontend").pull_ref,
            env=render.frontend_env(doc),
            ports=((store.port(doc, "frontend"), 8080),),
            wait_http=render.frontend_origin(doc),
        )
    )

    return specs
