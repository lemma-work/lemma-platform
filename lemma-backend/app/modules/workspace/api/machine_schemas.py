"""Request and response shapes for `/machines`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.workspace.domain.machine import Machine
from app.modules.workspace.domain.machine_access import MachineAccess

MachineKindName = Literal["default", "pod", "connected"]
MachineVisibilityName = Literal["private", "pod", "restricted"]
MachineStatusName = Literal[
    "running", "starting", "stopped", "offline", "error", "deleted"
]
AccessName = Literal["status", "use", "manage"]
TemplateName = Literal["workspace", "workspace-docker"]


class MachineCapabilitiesModel(BaseModel):
    processes: bool
    pty: bool
    python: bool
    files: bool
    previews: bool
    browser: bool
    docker: bool


class MachineResponse(BaseModel):
    id: UUID
    ref: str = Field(
        description=(
            "How to name this machine in a call: `me`, `pod:<pod-id>/<name>` "
            "or `mine:<name>`. In a URL path use `:` for the slash."
        )
    )
    kind: MachineKindName
    name: str
    display_name: str
    template: TemplateName
    size: str = Field(description="`<vcpu>x<memory GB>`, e.g. `2x4`.")
    fabric: str
    capabilities: MachineCapabilitiesModel
    visibility: MachineVisibilityName
    status: MachineStatusName
    access: AccessName = Field(description="What the caller may do with it.")
    pod_id: UUID | None = None
    run_slots: int
    home: str
    last_used_at: datetime | None = None
    compute_units_this_month: float | None = Field(
        default=None,
        description="Compute used this calendar month (1 CU = one hour at 2x4).",
    )


class MachineListResponse(BaseModel):
    machines: list[MachineResponse]


_ACCESS_NAMES: dict[MachineAccess, AccessName] = {
    MachineAccess.STATUS: "status",
    MachineAccess.USE: "use",
    MachineAccess.MANAGE: "manage",
}


def machine_response(
    machine: Machine,
    access: MachineAccess,
    *,
    compute_units: float | None = None,
) -> MachineResponse:
    return MachineResponse(
        id=machine.id,
        ref=machine.ref,
        kind=machine.kind.value,  # type: ignore[arg-type]
        name=machine.name,
        display_name=machine.display_name,
        template=machine.template.value,  # type: ignore[arg-type]
        size=machine.size.label,
        fabric=machine.fabric.value,
        capabilities=MachineCapabilitiesModel(**machine.capabilities.to_json()),
        visibility=machine.visibility.value,  # type: ignore[arg-type]
        status=machine.status.value,  # type: ignore[arg-type]
        access=_ACCESS_NAMES.get(access, "status"),
        pod_id=machine.pod_id,
        run_slots=machine.run_slots,
        home=machine.home,
        last_used_at=machine.last_used_at,
        compute_units_this_month=compute_units,
    )


StartupState = Literal["ready", "downloading", "starting", "asleep", "unavailable"]


class MachineStartupResponse(BaseModel):
    """Whether the machine is ready and, if not, what it is doing. Never wakes it."""

    state: StartupState = Field(
        description=(
            "`ready`: running. `downloading`: fetching its image, which the first "
            "start after an update does. `starting`: coming up. `asleep`: not "
            "running, and starts on first use. `unavailable`: could not be asked."
        )
    )
    detail: str | None = None
    done_mb: int | None = None
    total_mb: int | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None


ResetScopeName = Literal[
    "processes", "runs", "caches", "docker", "browser-session", "home"
]


class ResetRequest(BaseModel):
    scopes: list[ResetScopeName] = Field(min_length=1)
    confirm: bool = Field(
        default=False,
        description="Required for `home`, which replaces the machine's disk.",
    )
    forget_logins: bool = Field(
        default=False,
        description=(
            "With `home`: do not restore the browser's logins onto the new "
            "disk. Logins survive every other scope untouched."
        ),
    )


class ResetScopeResult(BaseModel):
    scope: ResetScopeName
    freed_bytes: int | None = None
    detail: str | None = None


class ResetResponse(BaseModel):
    results: list[ResetScopeResult]


class SizeOffer(BaseModel):
    template: TemplateName
    sizes: list[str]


class MachinePolicyResponse(BaseModel):
    """What a person or pod may create, for a screen to offer and no more."""

    default_size: str
    templates: list[SizeOffer]
    max_pod_machines: int | None = None
    max_connected_machines: int | None = None
    max_running: int | None = None


class CreatePodMachineRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    display_name: str | None = Field(default=None, max_length=255)
    template: TemplateName = "workspace"
    size: str | None = Field(
        default=None, description="Defaults to the plan's default size."
    )
    visibility: Literal["pod", "restricted"] = "pod"
    run_slots: int = Field(default=1, ge=1, le=16)


class UpdatePodMachineRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    size: str | None = None
    visibility: Literal["pod", "restricted"] | None = None
    run_slots: int | None = Field(default=None, ge=1, le=16)


class MachineGrantsModel(BaseModel):
    """Who may use a pod machine beyond what visibility already allows.

    Agents and functions by name; people by user id. A RESTRICTED machine is
    usable only by those named here (and pod admins).
    """

    agents: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    members: list[UUID] = Field(default_factory=list)
