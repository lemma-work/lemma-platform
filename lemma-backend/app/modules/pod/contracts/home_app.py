"""The app a pod opens on (``config.home_app``), for the modules that set it.

A pod's apps are listed oldest first, an order nobody chose, and the app it
opens on was simply the first of them. ``home_app`` is the choice, by app
name: a person makes it from the pod's apps, and ``pod_bundle`` carries the
one a bundle's ``pod.json`` names. Both go through the pod's own update, so
the same permission (``pod.update``) and the same field-wise config merge
apply, and the rest of the config is never written back over.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context


async def read_home_app(
    session: AsyncSession, pod_id: UUID, *, for_update: bool = False
) -> str | None:
    """The app the pod opens on, or ``None`` when nobody chose one.

    ``for_update`` locks the pod's row until the transaction ends, as the
    pod's own update does: a choice read so, then written in the same
    transaction, cannot pass over one a person saved in between."""
    from app.modules.pod.domain.pod_entities import PodConfig
    from app.modules.pod.infrastructure.models import Pod

    query = select(Pod.config).where(Pod.id == pod_id, Pod.is_deleted.is_(False))
    raw = await session.scalar(query.with_for_update() if for_update else query)
    return PodConfig.from_raw(raw).home_app if raw is not None else None


async def set_home_app(
    uow, *, pod_id: UUID, app_name: str | None, ctx: Context
) -> None:
    """Choose the app the pod opens on as ``ctx`` (``None`` hands it back to
    the apps' order); no commit. Refused like any pod update ``ctx`` may not
    make."""
    from app.modules.pod.api.dependencies import get_pod_service
    from app.modules.pod.domain.pod_entities import PodConfig, PodUpdateEntity

    if ctx.user_id is None:
        raise ValueError("Choosing a pod's app needs a person to choose it")
    await get_pod_service(uow).update_pod(
        pod_id,
        PodUpdateEntity(config=PodConfig(home_app=app_name)),
        requester_user_id=ctx.user_id,
        ctx=ctx,
    )


__all__ = ["read_home_app", "set_home_app"]
