"""Why a queued private message can no longer take its personal route.

Not a domain error: nothing raises it to an HTTP caller. Preparing a message
for a verified person's private route reads the route, and the route can vanish
between that and the run -- the identity unlinked, the chat closed, the account
gone. The turn starter catches this and sends the message through ordinary
ingress instead.

A module of its own, rather than beside the route checks in
``services/personal_dm_routes``, so that catching it does not import them: they
reach identity onboarding, and through it the sign-in stack, which the worker's
turn starter otherwise never loads.
"""

from __future__ import annotations


class PersonalRouteUnavailable(ValueError):
    """A verified person's private route to their pod can no longer carry a DM."""


__all__ = ["PersonalRouteUnavailable"]
