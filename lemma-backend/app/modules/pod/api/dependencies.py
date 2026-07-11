"""Pod module dependencies."""

from typing import Annotated
from fastapi import Depends

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import pod_from_path, require_action
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.pod.services.pod_service import PodService
from app.modules.pod.services.pod_member_service import PodMemberService
from app.modules.pod.services.pod_join_request_service import PodJoinRequestService
from app.composition.icons import create_icon_service
from app.modules.pod.infrastructure.pod_repositories import (
    PodJoinRequestRepository,
    PodRepository,
    PodMemberRepository,
)
from app.composition.pod_identity_wiring import create_organization_repository
from app.core.authorization.permissions import Permissions

from app.modules.pod.domain.pod_entities import PodRole
from app.modules.pod.services.pod_role_service import PodRoleService
from app.modules.pod.services.authorization_factory import create_authorization_service


def get_pod_service(
    uow: UoWDep,
) -> PodService:
    """Provide PodService with UoW-backed repositories."""
    message_bus = get_message_bus()
    return PodService(
        pod_repository=PodRepository(uow, message_bus=message_bus),
        pod_member_repository=PodMemberRepository(uow, message_bus=message_bus),
        organization_repository=create_organization_repository(uow),
        pod_role_service=PodRoleService(uow),
        authorization_service=create_authorization_service(uow),
        icon_service=create_icon_service(),
    )


def get_pod_member_service(
    uow: UoWDep,
) -> PodMemberService:
    """Provide PodMemberService."""
    message_bus = get_message_bus()
    return PodMemberService(
        pod_member_repository=PodMemberRepository(uow, message_bus=message_bus),
        pod_repository=PodRepository(uow, message_bus=message_bus),
        organization_repository=create_organization_repository(uow),
        pod_role_service=PodRoleService(uow),
    )


PodServiceDep = Annotated[PodService, Depends(get_pod_service)]
PodMemberServiceDep = Annotated[PodMemberService, Depends(get_pod_member_service)]


def get_pod_role_service(uow: UoWDep) -> PodRoleService:
    return PodRoleService(uow)


PodRoleServiceDep = Annotated[PodRoleService, Depends(get_pod_role_service)]


def get_pod_join_request_service(
    uow: UoWDep,
) -> PodJoinRequestService:
    message_bus = get_message_bus()
    return PodJoinRequestService(
        pod_join_request_repository=PodJoinRequestRepository(
            uow, message_bus=message_bus
        ),
        pod_member_repository=PodMemberRepository(uow, message_bus=message_bus),
        pod_repository=PodRepository(uow, message_bus=message_bus),
        organization_repository=create_organization_repository(uow),
        pod_role_service=PodRoleService(uow),
    )


PodJoinRequestServiceDep = Annotated[
    PodJoinRequestService, Depends(get_pod_join_request_service)
]


def require_pod_role(required_role: PodRole):
    """Dependency factory for pod role checks backed by authz actions."""
    if required_role == PodRole.VIEWER:
        return require_action(Permissions.POD_READ, pod_from_path)
    if required_role == PodRole.EDITOR:
        return require_action(Permissions.POD_UPDATE, pod_from_path)
    return require_action(Permissions.POD_DELETE, pod_from_path)


# Pre-created dependencies for common roles
PodViewerDep = require_pod_role(PodRole.VIEWER)
PodEditorDep = require_pod_role(PodRole.EDITOR)
PodAdminDep = require_pod_role(PodRole.ADMIN)
