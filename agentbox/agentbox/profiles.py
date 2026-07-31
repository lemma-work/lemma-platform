from __future__ import annotations

from dataclasses import dataclass

from agentbox.domain import (
    AgentBoxError,
    ErrorCode,
    RetryDisposition,
    SandboxCapability,
    SandboxProfileRef,
    WorkloadKind,
)


@dataclass(frozen=True, slots=True)
class DockerProfileArtifact:
    image: str
    command: tuple[str, ...]
    readiness_argv: tuple[str, ...]
    published_ports: tuple[int, ...] = ()
    runtime_port: int | None = None

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("Docker profile image cannot be empty")
        if self.runtime_port is None and not self.readiness_argv:
            raise ValueError(
                "Docker profiles without a runtime port need a readiness command"
            )
        if any(port < 1 or port > 65535 for port in self.published_ports):
            raise ValueError("Docker published ports must be in 1..65535")
        if (
            self.runtime_port is not None
            and self.runtime_port not in self.published_ports
        ):
            raise ValueError("Docker runtime port must be included in published_ports")


@dataclass(frozen=True, slots=True)
class E2BProfileArtifact:
    template_id: str
    build_id: str

    def __post_init__(self) -> None:
        if not self.template_id or not self.build_id:
            raise ValueError("E2B template and build IDs are required")

    @property
    def immutable_reference(self) -> str:
        """Reference one exact E2B build rather than a mutable template tag."""

        return f"{self.template_id}:{self.build_id}"


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    ref: SandboxProfileRef
    workload_kind: WorkloadKind
    runtime_abi: str
    capabilities: frozenset[SandboxCapability]
    allowed_roots: tuple[str, ...]
    docker: DockerProfileArtifact | None
    e2b: E2BProfileArtifact | None

    def __post_init__(self) -> None:
        if not self.runtime_abi:
            raise ValueError("profile runtime ABI cannot be empty")
        if not self.allowed_roots:
            raise ValueError("profile must declare at least one filesystem root")
        if any(not root.startswith("/") for root in self.allowed_roots):
            raise ValueError("profile filesystem roots must be absolute")


class ProfileRegistry:
    def __init__(self, profiles: tuple[SandboxProfile, ...]) -> None:
        by_digest: dict[str, SandboxProfile] = {}
        for profile in profiles:
            if profile.ref.digest in by_digest:
                raise ValueError(f"duplicate profile digest: {profile.ref.digest}")
            by_digest[profile.ref.digest] = profile
        self._by_digest = by_digest

    def resolve(
        self,
        ref: SandboxProfileRef,
        *,
        workload_kind: WorkloadKind,
    ) -> SandboxProfile:
        profile = self._by_digest.get(ref.digest)
        if profile is None or profile.ref != ref:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "sandbox profile is not published in this AgentBox release",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        if profile.workload_kind != workload_kind:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "sandbox profile does not match workload kind",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        return profile

    def docker_artifact(
        self,
        ref: SandboxProfileRef,
        *,
        workload_kind: WorkloadKind,
    ) -> DockerProfileArtifact:
        profile = self.resolve(ref, workload_kind=workload_kind)
        if profile.docker is None:
            raise AgentBoxError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "profile has no Docker artifact",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        return profile.docker

    def e2b_artifact(
        self,
        ref: SandboxProfileRef,
        *,
        workload_kind: WorkloadKind,
    ) -> E2BProfileArtifact:
        profile = self.resolve(ref, workload_kind=workload_kind)
        if profile.e2b is None:
            raise AgentBoxError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "profile has no E2B artifact",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        return profile.e2b
