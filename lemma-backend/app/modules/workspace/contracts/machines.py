"""Naming a machine from another module's route.

A module that serves something *on* a machine -- web_login's sign-ins and
logins -- resolves the machine the same way every `/machines` route does, so
there is one authorization rule for reaching a machine wherever the route
lives.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from app.modules.workspace.domain.machine_access import MachineAccess


async def machine_id_for_request(
    request: Request, ref: str, *, manage: bool = False
) -> UUID:
    """The machine ``ref`` names, if this request may use (or manage) it.

    Raises the machine module's own refusals, which the API maps to 404/403.
    """
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.api.controllers.machine_dependencies import (
        resolve_for_request,
    )

    resolved, _caller = await resolve_for_request(
        request,
        get_uow_factory(),
        ref,
        need=MachineAccess.MANAGE if manage else MachineAccess.USE,
    )
    return resolved.machine.id


__all__ = ["machine_id_for_request"]
