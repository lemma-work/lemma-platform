"""A `list_active_for_routing` stand-in that narrows the way the statement does.

Surface routing asks the database for the surfaces an inbound event could be
for, and every narrowing it passes exists to *remove* a surface that would
otherwise be routed to -- a receiver's own list, a workspace, shared system
credentials. A double that ignores them and hands back everything certifies a
result the database cannot produce, and the test then proves the opposite of
what it says: that routing is correct when the narrowing is absent.

So the predicates live here once, next to the tests that need them, rather than
being re-stubbed per file with a fixed list.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID


def routing_surfaces_double(surfaces: Sequence):
    """An async callable matching ``SurfaceRepository.list_active_for_routing``.

    ``surfaces`` stands in for the rows the platform's statement would return
    before narrowing; ACTIVE-ness and the live-pod join are the repository's own
    and are assumed, exactly as the fixed-list doubles this replaces assumed
    them.
    """

    async def _list_active_for_routing(
        surface_type: str,
        *,
        surface_ids: Collection[UUID] | None = None,
        external_workspace_id: str | None = None,
        system_credentials_only: bool = False,
    ) -> list:
        chosen = list(surfaces)
        if surface_ids is not None:
            # An empty collection means "none of them", as `IN ()` does.
            allowed = set(surface_ids)
            chosen = [surface for surface in chosen if surface.id in allowed]
        if external_workspace_id:
            chosen = [
                surface
                for surface in chosen
                if surface.external_workspace_id == external_workspace_id
            ]
        if system_credentials_only:
            chosen = [
                surface
                for surface in chosen
                if surface.account_id is None
                and str(surface.credential_mode) == "SYSTEM"
            ]
        return chosen

    return _list_active_for_routing
