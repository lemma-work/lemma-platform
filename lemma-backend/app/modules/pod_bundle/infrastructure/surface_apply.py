"""Applying one surface out of a bundle.

Split out of ``applier.py`` because it is by a wide margin the largest step
there -- roughly a fifth of the file -- and that file is past the 600-line
ceiling the architecture ratchet sets. Nothing about it is shared, which is what
makes it the clean cut.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from lemma_pod_bundle.apply_fields import SURFACE_APPLY_FIELDS

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.domain.state import PlanStep


async def apply_surface(
    step: PlanStep,
    *,
    uow,
    ctx,
    pod_id: UUID,
    load: Callable[[str, str], dict[str, Any]],
    warnings: list[str],
) -> None:
    """Create or update a pod surface, bound to the pod connector the bundle
    names by slug -- the importing pod's own, so its account is that pod's
    decision and none travels in the bundle. A pod may have
    several surfaces per platform, each addressed by a stable pod-unique
    ``name`` (defaults to the lowercased platform), so import is an idempotent
    upsert keyed by that name — mirroring the surface create/update
    controllers (reusing their config helpers) so an imported connector
    behaves exactly like a hand-configured one."""
    from app.modules.agent.contracts.provisioning import get_agent
    from app.modules.agent_surfaces.contracts import (
        AgentSurfaceEntity,
        SurfaceCreateRequest,
        SurfacePlatform,
    )
    from app.modules.agent_surfaces.contracts.provisioning import (
        apply_surface_config,
        build_surface_config,
        create_surface,
        get_surface_by_name,
        update_surface,
    )

    payload = await _without_deprecated_account(
        load("surfaces", step.name),
        step.name,
        uow=uow,
        ctx=ctx,
        pod_id=pod_id,
        warnings=warnings,
    )
    platform_raw = payload.get("platform")
    if not platform_raw:
        # An up-to-date exporter always writes platform explicitly; a
        # missing value means a stale/hand-edited bundle, not something to
        # silently paper over by guessing from the directory name.
        raise PodBundleDomainError(
            f"Surface '{step.name}' is missing its platform — re-export this bundle.",
            code="POD_BUNDLE_SURFACE_PLATFORM",
        )
    try:
        platform = SurfacePlatform(str(platform_raw).upper())
    except ValueError as exc:
        raise PodBundleDomainError(
            f"Unsupported surface platform '{platform_raw}'.",
            code="POD_BUNDLE_SURFACE_PLATFORM",
        ) from exc

    # The surface's pod-unique name (defaults to the lowercased platform);
    # the upsert is keyed by it so several surfaces of the same platform
    # round-trip.
    resolved_name = str(
        payload.get("name") or ""
    ).strip() or AgentSurfaceEntity.default_name_for(platform)

    # Only the create-request fields (extra='forbid'); drop export-only keys.
    # The allow-list is the one lemma-cli's direct import builds from, so the
    # two importers keep the same fields.
    request = SurfaceCreateRequest.model_validate(
        {
            "platform": platform.value,
            "name": resolved_name,
            **{
                key: value
                for key, value in payload.items()
                if key in SURFACE_APPLY_FIELDS
            },
        }
    )

    agent = (
        await get_agent(uow, pod_id=pod_id, name=request.default_agent_name, ctx=ctx)
        if request.default_agent_name
        else None
    )

    existing = await get_surface_by_name(uow, pod_id=pod_id, name=resolved_name)

    if existing is None:
        config = await build_surface_config(
            uow,
            pod_id=pod_id,
            platform=platform,
            config_input=request.config,
            ctx=ctx,
        )
        surface = await create_surface(
            uow,
            pod_id=pod_id,
            agent_id=agent.id if agent else None,
            agent_name=agent.name if agent else None,
            platform=platform,
            name=resolved_name,
            config=config,
            credential_mode=request.credential_mode,
            pod_connector_slug=request.pod_connector,
            ctx=ctx,
        )
        if not request.is_enabled:
            await update_surface(uow, surface_id=surface.id, is_active=False, ctx=ctx)
        return

    config = await apply_surface_config(
        uow,
        pod_id=pod_id,
        platform=platform,
        existing=existing.config,
        config_input=request.config,
        ctx=ctx,
    )
    await update_surface(
        uow,
        surface_id=existing.id,
        agent_id=agent.id if agent else None,
        update_agent_id="default_agent_name" in request.model_fields_set,
        config=config,
        credential_mode=(
            request.credential_mode
            if "credential_mode" in request.model_fields_set
            else None
        ),
        pod_connector_slug=request.pod_connector,
        is_active=(
            request.is_enabled if "is_enabled" in request.model_fields_set else None
        ),
        ctx=ctx,
    )


async def _without_deprecated_account(
    payload: dict[str, Any],
    name: str,
    *,
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    pod_id: UUID,
    warnings: list[str],
) -> dict[str, Any]:
    """A surface exported before pod connectors, which named an account.

    Translated as a schedule's is: an account the importer supplied becomes
    the pod connector serving it to them, and the import says so. Without one
    the surface is imported on the platform's own bot, and the import says
    that. It used to be dropped here without a word, while lemma-cli's import
    of the same bundle warned.
    """
    if "account_id" not in payload or payload.get("pod_connector"):
        return payload
    account_id = _as_uuid(payload.get("account_id"))
    if account_id is None:
        warnings.append(
            f"Surface '{name}' named a connected account, which bundles no "
            "longer carry; it runs on the platform's own bot until you name the "
            "pod connector it should run as."
        )
        return payload
    from app.modules.connectors.contracts.pod_connectors import (
        pod_connector_for_account,
    )

    binding = await pod_connector_for_account(
        uow, ctx, pod_id=pod_id, account_id=account_id
    )
    warnings.append(
        f"Surface '{name}' named an account (deprecated); it runs as the pod "
        f"connector '{binding.slug}' instead."
    )
    return {**payload, "pod_connector": binding.slug}


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
