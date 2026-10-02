"""Decisions module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.decisions.api.deciders_controller import router as deciders
    from app.modules.decisions.api.decisions_controller import router as decisions

    return [decisions, deciders]


def _register_streaq() -> None:
    import app.modules.decisions.events.tasks  # noqa: F401


def _resource_names():
    """How a decider is addressed by name in a grant."""
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.decisions.infrastructure.models import DeciderModel

    return (
        (
            ResourceType.DECIDER,
            ResourceNameTable(DeciderModel.id, DeciderModel.pod_id, DeciderModel.name),
        ),
    )


module = LemmaModule(
    name="decisions",
    routers=_routers,
    register_streaq=_register_streaq,
    resource_names=_resource_names,
)
