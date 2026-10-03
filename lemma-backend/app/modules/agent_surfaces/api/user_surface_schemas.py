"""Wire shapes for the user-scoped ``/surfaces/me`` routes.

Kept beside the pod-scoped schemas rather than inside them: these answer for a
person across every pod they belong to, which is a different question from what
one pod has configured.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, model_validator

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class UserSurfaceItem(BaseModel):
    """One of the current user's surfaces (across any pod they belong to)."""

    id: UUID
    name: str
    pod_id: UUID
    platform: SurfacePlatform
    agent_id: UUID | None = None
    is_default: bool = False
    # True when another surface in this list answers at the same address (the
    # deployment's shared bot/number). Only these are a choice; a pod's own bot
    # has its own handle, so a message to it can only arrive there.
    shares_address: bool = False


class AvailablePodItem(BaseModel):
    """A pod that could answer this user on a shared-bot platform."""

    pod_id: UUID
    name: str
    organization_name: str | None = None


class UserSurfacePlatformGroup(BaseModel):
    """All of a user's surfaces for one platform. ``conflict`` is true when two
    of them answer at the same address, so the user has to say which pod hears
    them (the ``shares_address`` surfaces are the ones to choose between).

    On a platform with a shared bot (WhatsApp), ``available_pods`` lists every
    pod the user may be answered from -- including pods with no surface there
    yet -- and ``default_pod_id`` names the one that answers now. The group is
    present even when the user has no surface on the platform at all."""

    platform: SurfacePlatform
    conflict: bool = False
    default_surface_id: UUID | None = None
    default_pod_id: UUID | None = None
    surfaces: list[UserSurfaceItem]
    available_pods: list[AvailablePodItem] = []


class UserSurfacesResponse(BaseModel):
    groups: list[UserSurfacePlatformGroup]


class SetDefaultSurfaceRequest(BaseModel):
    """Pick which surface answers this user for ``platform`` when several could.

    Exactly one of ``surface_id`` (a surface that already exists) or ``pod_id``
    (a pod to be answered from on the platform's shared bot, whose surface is
    made if it has none yet)."""

    platform: SurfacePlatform
    surface_id: UUID | None = None
    pod_id: UUID | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "SetDefaultSurfaceRequest":
        if (self.surface_id is None) == (self.pod_id is None):
            raise ValueError("Give exactly one of surface_id or pod_id.")
        return self
