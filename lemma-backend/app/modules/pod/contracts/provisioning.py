"""What a bundle does to the pod it is exporting from, or importing into.

Replaces `app/composition/pod_bundle_pod.py`, which published `PodService`,
`PodMemberService` and a `PodRepository` whose entire body was a `__new__` doing
a lazy import -- a class that existed to be monkeypatched, not to be used. Three
services, for two questions and one write.

`append_recipe` is the write, and it is one operation rather than the read,
merge and update the caller used to perform. The merge is not incidental:
`PodService.update_pod` merges `config` field-wise, so a caller that hands it a
recipe list alone resets `join_policy` and `default_runtime` to their defaults.
That is pod's own update semantics, and knowing it was the price of `pod_bundle`
holding the service.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
service layer, and everything importing any pod contract would otherwise pay for
it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.pod.api.dependencies import get_pod_service
from app.modules.pod.domain.pod_entities import PodEntity, PodRecipe, PodUpdateEntity
from app.modules.pod.infrastructure.pod_repositories import PodRepository


async def get_pod(uow, *, pod_id: UUID) -> PodEntity | None:
    """The pod itself, or ``None`` when it no longer exists.

    An unauthorized read: the export path holds a context that has already
    required `POD_READ` on this pod, so ``None`` here means the pod was deleted
    underneath a running export, not that the caller may not see it.
    """
    return await PodRepository(uow).get(pod_id)


async def append_recipe(
    uow,
    *,
    pod_id: UUID,
    recipe: PodRecipe,
    requester_user_id: UUID,
    ctx: Context,
) -> None:
    """Record that this pod was built from a bundle, keeping the rest of config.

    Raises whatever the pod's own read and update raise -- the caller has just
    finished importing into this pod, so a pod that has gone missing or a
    context that may not update it are both real failures, not conditions to
    paper over.
    """
    pod_service = get_pod_service(uow)
    pod = await pod_service.get_pod(pod_id, requester_user_id)
    if pod is None:
        raise LookupError(f"pod {pod_id} no longer exists")
    config = pod.config.model_copy(update={"recipes": [*pod.config.recipes, recipe]})
    await pod_service.update_pod(
        pod_id,
        PodUpdateEntity(config=config),
        requester_user_id=requester_user_id,
        ctx=ctx,
    )


__all__ = ["append_recipe", "get_pod"]
