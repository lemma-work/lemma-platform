"""Pod-import module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.pod_import.api.controllers.export_controller import (
        router as export_router,
    )
    from app.modules.pod_import.api.controllers.github_controller import (
        github_import_into_pod_router,
        github_import_router,
        github_publish_router,
    )
    from app.modules.pod_import.api.controllers.import_controller import (
        new_pod_import_router,
        router as import_router,
    )

    return [
        import_router,
        new_pod_import_router,
        export_router,
        github_publish_router,
        github_import_router,
        github_import_into_pod_router,
    ]


module = LemmaModule(name="pod_import", routers=_routers)
