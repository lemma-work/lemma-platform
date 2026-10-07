"""What a completed import leaves on the pod it built.

Its own module because the import handler is a ratcheted file: the recipe and
the description are one short unit of work after the last step, and the
handler only needs to call it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext
from app.modules.pod_bundle.domain.state import ImportState


async def record_recipe(worker_ctx: AppWorkerContext, state: ImportState) -> None:
    """Append a durable :class:`PodRecipe` to the pod's config in a short UoW.

    The bundle's description travels with it and lands only on a pod that has
    none: a template imported into a teammate hired with a job line keeps that
    line, and one installed into an empty pod is told what it is for.
    """
    # Imported here, as the handler always did: the pod module's contracts are
    # only needed once an import has finished, not when the worker loads.
    from app.modules.pod.contracts import PodRecipe
    from app.modules.pod.contracts.provisioning import append_recipe

    recipe = PodRecipe(
        kind=state.source.kind.value,
        name=(state.plan.bundle_name if state.plan else None),
        repo_url=state.source.repo_url or state.source.url,
        format_version=(state.plan.format_version if state.plan else None),
        imported_at=datetime.now(timezone.utc),
        imported_by=state.user_id,
    )
    async with uow_scope(worker_ctx.uow_factory) as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=state.user_id, pod_id=state.pod_id
        )
        async with context_scope(ctx):
            await append_recipe(
                uow,
                pod_id=state.pod_id,
                recipe=recipe,
                requester_user_id=state.user_id,
                ctx=ctx,
                description_if_empty=(state.plan.description if state.plan else None),
            )
