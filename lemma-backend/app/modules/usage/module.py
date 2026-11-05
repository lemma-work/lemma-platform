"""Usage module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.usage.api.controllers import router as usage

    from app.modules.usage.api.self_controllers import router as self_usage

    return [usage, self_usage]


def _register_periodic_work() -> None:
    # Worker only: the gauge loop that samples it runs there.
    import app.modules.usage.infrastructure.active_users_gauge  # noqa: F401


module = LemmaModule(
    name="usage",
    routers=_routers,
    register_periodic_work=_register_periodic_work,
)
