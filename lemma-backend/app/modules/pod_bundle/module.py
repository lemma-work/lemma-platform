"""Pod bundle module registration.

Export, import, and GitHub sharing of pods as bundles — long-running work runs
as admin-class work items whose state is authoritative in PostgreSQL (`pod_bundle_jobs`,
`pod_bundle_job_steps`) and mirrored to Redis for realtime. The docstring here
used to say the module owned no tables and needed no migrations; both stopped
being true when job state became durable, which left an operator with two
growing tables nothing told them to watch.
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


def _register_work() -> None:
    # Every process: the API writes the job items with the job's state row,
    # the worker runs them. The bodies are imported when an item runs.
    import app.modules.pod_bundle.events.work  # noqa: F401


def _register_periodic_work() -> None:
    import app.modules.pod_bundle.events.sweep  # noqa: F401


module = LemmaModule(
    name="pod_bundle",
    routers=_routers,
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
)
