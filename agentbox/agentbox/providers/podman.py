from __future__ import annotations

from agentbox.providers.docker import DockerSandboxProvider


class PodmanSandboxProvider(DockerSandboxProvider):
    cli_name = "podman"
    namespace = "podman"
    provider_name = "podman"
