"""App module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.apps.api.controllers.app_controller import router as app_router
    from app.modules.apps.api.controllers.public_app_controller import (
        router as public_app,
    )
    from app.modules.apps.api.controllers.public_sdk_controller import (
        router as public_sdk,
    )

    return [app_router, public_app, public_sdk]


def _register_streaq() -> None:
    import app.modules.apps.events.tasks  # noqa: F401


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.apps.infrastructure.models import AppModel

    return (
        (
            ResourceType.APP,
            ResourceNameTable(AppModel.id, AppModel.pod_id, AppModel.name),
        ),
    )


module = LemmaModule(
    name="apps",
    resource_names=_resource_names,
    routers=_routers,
    register_streaq=_register_streaq,
)
