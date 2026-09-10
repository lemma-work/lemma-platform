"""Pod module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.pod.api.controllers.pod_controller import (
        router as pod,
        organization_router as pod_by_organization,
    )
    from app.modules.pod.api.controllers.pod_member_controller import router as member
    from app.modules.pod.api.controllers.pod_permission_controller import (
        router as permission,
    )
    from app.modules.pod.api.controllers.resource_access_controller import (
        router as resource_access,
    )
    from app.modules.pod.api.controllers.resource_preview_controller import (
        router as resource_preview,
    )
    from app.modules.pod.api.controllers.pod_role_controller import router as role
    from app.modules.pod.api.controllers.pod_join_request_controller import (
        router as join_request,
    )

    return [
        pod,
        pod_by_organization,
        member,
        permission,
        resource_access,
        resource_preview,
        role,
        join_request,
    ]


def _event_routers():
    from app.modules.pod.events.pod_handlers import router

    return [router]


def _pod_liveness():
    """The reader core's enumeration guard asks; see `contracts/liveness.py`."""
    from app.modules.pod.contracts.liveness import pod_is_live

    return pod_is_live


module = LemmaModule(
    name="pod",
    pod_liveness=_pod_liveness,
    routers=_routers,
    event_routers=_event_routers,
    stream_groups=(("pod_events", "pod-join-request-events"),),
)
