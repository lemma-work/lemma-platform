"""Pod bundle module registration.

Export, import, and GitHub sharing of pods as bundles — long-running work runs
as streaq jobs with ephemeral Redis state (see
``docs/design/pod-bundle-share-import.md``). No migrations: this module owns no
tables.
"""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.pod_bundle.api.controllers.export_controller import (
        router as export_router,
    )
    from app.modules.pod_bundle.api.controllers.import_controller import (
        router as import_router,
    )
    from app.modules.pod_bundle.api.controllers.publish_controller import (
        router as publish_router,
    )

    return [import_router, export_router, publish_router]


def _register_streaq() -> None:
    import app.modules.pod_bundle.events.handlers  # noqa: F401


module = LemmaModule(
    name="pod_bundle",
    routers=_routers,
    register_streaq=_register_streaq,
)
