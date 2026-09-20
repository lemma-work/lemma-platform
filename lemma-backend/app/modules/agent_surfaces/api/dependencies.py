"""What FastAPI needs to inject a surfaces object, and nothing else.

The objects themselves are built in `agent_surfaces/composition.py`. This file
is the adapter between that and a route signature: a `Depends` wrapper carrying
`UoWDep` so the framework knows how to make the unit of work, and an `Annotated`
alias so a controller can name the type it wants.

Nothing outside `api/` should import from here. It used to -- twelve times,
including from `contracts/`, which is what this module publishes to the rest of
the backend -- and that made the HTTP layer the composition root for a module
whose objects have nothing to do with HTTP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.composition import (
    build_member_reach,
    build_notification_service,
    build_surface_connection_resolver,
    build_surface_service,
    build_surface_webhook_security_service,
    build_telegram_manager_service,
    build_user_surfaces_service,
)
from app.modules.agent_surfaces.services.member_reach import MemberReach
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
)
from app.modules.agent_surfaces.services.surface_connection_resolver import (
    SurfaceConnectionResolver,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.user_surfaces_service import (
    UserSurfacesService,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)


def get_surface_service(uow: UoWDep) -> AgentSurfaceService:
    return build_surface_service(uow)


def get_surface_connection_resolver(uow: UoWDep) -> SurfaceConnectionResolver:
    return build_surface_connection_resolver(uow)


def get_member_reach(uow: UoWDep) -> MemberReach:
    return build_member_reach(uow)


def get_notification_service(uow: UoWDep) -> NotificationService:
    return build_notification_service(uow)


def get_user_surfaces_service(uow: UoWDep) -> UserSurfacesService:
    return build_user_surfaces_service(uow)


def get_surface_webhook_security_service(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> SurfaceWebhookSecurityService:
    return build_surface_webhook_security_service(uow_factory)


def get_telegram_manager_service(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> TelegramManagerService:
    return build_telegram_manager_service(uow_factory)


SurfaceServiceDep = Annotated[AgentSurfaceService, Depends(get_surface_service)]
SurfaceConnectionResolverDep = Annotated[
    SurfaceConnectionResolver, Depends(get_surface_connection_resolver)
]
UserSurfacesServiceDep = Annotated[
    UserSurfacesService, Depends(get_user_surfaces_service)
]
MemberReachDep = Annotated[MemberReach, Depends(get_member_reach)]
SurfaceWebhookSecurityServiceDep = Annotated[
    SurfaceWebhookSecurityService, Depends(get_surface_webhook_security_service)
]
TelegramManagerServiceDep = Annotated[
    TelegramManagerService, Depends(get_telegram_manager_service)
]
NotificationServiceDep = Annotated[
    NotificationService, Depends(get_notification_service)
]
