"""Analytics module registration."""

from app.core.registry import LemmaModule


def _register_analytics() -> None:
    # Registers every projection on the after-commit analytics sink. Every
    # process: the API commits most domain events, the worker the rest.
    import app.modules.analytics.events  # noqa: F401


module = LemmaModule(
    name="analytics",
    register_analytics=_register_analytics,
)
