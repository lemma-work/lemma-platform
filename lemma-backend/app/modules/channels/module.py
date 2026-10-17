"""Channels module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.channels.api.controller import router

    return [router]


def _register_work() -> None:
    # Every process: a merge of two contacts (API, a web visitor proving an
    # email) writes this through `ContactMerged`.
    import app.modules.channels.events.contact_merged_work  # noqa: F401


module = LemmaModule(name="channels", routers=_routers, register_work=_register_work)
