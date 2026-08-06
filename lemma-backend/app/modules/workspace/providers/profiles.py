"""What image a sandbox kind runs, and how to tell when it is up.

A profile pairs a name+digest with the artifact for one kind. The digest is
recorded on the sandbox row rather than only read from settings, because a
workspace holding a user's files keeps running the profile it was created with
when the configured digest moves -- replacing it on a config change would
restart every workspace in the fleet at deploy time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.modules.workspace.domain.sandbox import SandboxKind

WORKSPACE_RUNTIME_PORT = 8080
WORKSPACE_BROWSER_PORT = 4848
FUNCTION_RUNTIME_PORT = 8090


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    name: str
    digest: str
    image: str
    kind: SandboxKind
    runtime_port: int
    published_ports: tuple[int, ...]
    working_dir: str

    @property
    def is_function(self) -> bool:
        return self.kind is SandboxKind.FUNCTION


def workspace_profile(*, image: str | None = None) -> SandboxProfile:
    return SandboxProfile(
        name=settings.agentbox_workspace_profile_name,
        digest=settings.agentbox_workspace_profile_digest,
        image=image or settings.agentbox_workspace_image,
        kind=SandboxKind.WORKSPACE,
        runtime_port=WORKSPACE_RUNTIME_PORT,
        published_ports=(WORKSPACE_RUNTIME_PORT, WORKSPACE_BROWSER_PORT),
        working_dir="/workspace",
    )


def function_profile(*, image: str | None = None) -> SandboxProfile:
    return SandboxProfile(
        name=settings.agentbox_function_profile_name,
        digest=settings.agentbox_function_profile_digest,
        image=image or settings.agentbox_function_image,
        kind=SandboxKind.FUNCTION,
        runtime_port=FUNCTION_RUNTIME_PORT,
        published_ports=(FUNCTION_RUNTIME_PORT,),
        # Function control state lives entirely in /tmp so the image root can
        # stay read-only.
        working_dir="/tmp",
    )


def profile_for(kind: SandboxKind) -> SandboxProfile:
    return (
        function_profile() if kind is SandboxKind.FUNCTION else workspace_profile()
    )
