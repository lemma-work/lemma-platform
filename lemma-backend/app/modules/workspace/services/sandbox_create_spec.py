"""What a provider is asked to build, from the row and this provision's facts.

Kept apart from `SandboxService._provision` so the provision reads as the
sequence it is -- claim, build, wait, record -- and the spec's own rules sit
in one place.
"""

from __future__ import annotations

from datetime import datetime

from app.core.ports.plan_limits import SandboxSize
from app.modules.workspace.domain.sandbox import Sandbox
from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.profiles import SandboxProfile


def provider_create_spec(
    sandbox: Sandbox,
    profile: SandboxProfile,
    *,
    epoch: int,
    name: str,
    deadline_at: datetime,
    volume_name: str | None,
    size: SandboxSize | None,
    host_loopback: bool,
) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox.id,
        kind=sandbox.kind,
        epoch=epoch,
        name=name,
        image=profile.image,
        # The configured profile, not the row's: the row was just brought up
        # to date, and the container is stamped with this so the next ensure
        # can tell whether it is still current.
        profile_name=profile.name,
        profile_digest=profile.digest,
        deadline_at=deadline_at,
        volume_name=volume_name,
        mounts=sandbox.mounts,
        size=size,
        # The owner's loopback relay; see `host_loopback_policy`.
        host_loopback=host_loopback,
    )
