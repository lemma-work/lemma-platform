"""Contacts module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.contacts.api.controller import router

    return [router]


module = LemmaModule(name="contacts", routers=_routers)
