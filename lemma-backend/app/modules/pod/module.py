"""Pod module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.pod.api.controllers.pod_controller import router as pod
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
    from app.modules.pod.api.controllers.resource_access_request_controller import (
        router as resource_access_request,
    )
    from app.modules.pod.api.controllers.resource_access_invite_controller import (
        router as resource_access_invite,
    )
    from app.modules.pod.api.controllers.pod_role_controller import router as role
    from app.modules.pod.api.controllers.pod_join_request_controller import (
        router as join_request,
    )

    return [
        pod,
        member,
        permission,
        resource_access,
        resource_preview,
        resource_access_request,
        resource_access_invite,
        role,
        join_request,
    ]


def _event_routers():
    from app.modules.pod.events.pod_handlers import router

    return [router]


module = LemmaModule(
    name="pod",
    routers=_routers,
    event_routers=_event_routers,
    stream_groups=(
        ("pod_events", "pod-join-request-events"),
        # Redeeming resource invites when an account appears for their address.
        ("identity_events", "pod-resource-invite-redemption"),
    ),
)
