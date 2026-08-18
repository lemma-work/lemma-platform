"""What image a sandbox kind runs, and how to tell when it is up.

A profile pairs a name+digest with the artifact for one kind. The digest is
recorded on the sandbox row so that a running sandbox can be compared against
what is configured now: when the two differ the sandbox is replaced rather than
reused, because it is running an image the backend may no longer know how to
talk to. Moving the configured digest therefore does restart the fleet, and
that is the point -- it is the only lever that reaches a sandbox that already
exists. A sandbox is compute, not storage: on Docker and lemma_local the disk
is a separate object and is adopted across the replacement.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.workspace.config import workspace_settings
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


def _digest_for(image: str, configured: str) -> str:
    """The image's own digest when it has one, else the configured constant.

    The configured digest is a hand-bumped placeholder, and forgetting to move
    it is silent in the worst way: a release ships a new workspace image, every
    existing sandbox compares equal to what is configured, and each one keeps
    running the image it was built from. The comment on the setting says it was
    "last moved when the GitHub CLI was added" -- every image change since then
    reached only sandboxes that happened to be created afterwards.

    A pinned reference already carries the answer. Desktop and local installs
    resolve `lemma-workspace@sha256:...` from the release manifest, so the image
    identity *is* the digest and no one has to remember anything. A floating tag
    like `lemma-workspace:dev` carries nothing, and there the constant still
    applies -- a developer rebuilding the same tag does not want their fleet
    replaced on every build.
    """
    _, separator, digest = image.rpartition("@sha256:")
    return f"sha256:{digest}" if separator else configured


def workspace_profile(*, image: str | None = None) -> SandboxProfile:
    resolved_image = image or workspace_settings.workspace_image
    return SandboxProfile(
        name=workspace_settings.workspace_profile_name,
        digest=_digest_for(resolved_image, workspace_settings.workspace_profile_digest),
        image=resolved_image,
        kind=SandboxKind.WORKSPACE,
        runtime_port=WORKSPACE_RUNTIME_PORT,
        published_ports=(WORKSPACE_RUNTIME_PORT, WORKSPACE_BROWSER_PORT),
        working_dir="/workspace",
    )


def function_profile(*, image: str | None = None) -> SandboxProfile:
    resolved_image = image or workspace_settings.function_image
    return SandboxProfile(
        name=workspace_settings.function_profile_name,
        digest=_digest_for(resolved_image, workspace_settings.function_profile_digest),
        image=resolved_image,
        kind=SandboxKind.FUNCTION,
        runtime_port=FUNCTION_RUNTIME_PORT,
        published_ports=(FUNCTION_RUNTIME_PORT,),
        # Function control state lives entirely in /tmp so the image root can
        # stay read-only.
        working_dir="/tmp",
    )


def profile_for(kind: SandboxKind) -> SandboxProfile:
    return function_profile() if kind is SandboxKind.FUNCTION else workspace_profile()
