from __future__ import annotations

from agentbox.providers.docker import DockerSandboxProvider


class PodmanSandboxProvider(DockerSandboxProvider):
    """Drive Podman's Docker-compatible API with the bundled Docker client."""

    cli_name = "docker"
    namespace = "podman"
    provider_name = "podman"
