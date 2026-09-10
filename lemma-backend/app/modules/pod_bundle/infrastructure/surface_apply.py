"""Applying one surface out of a bundle.

Split out of ``applier.py`` because it is by a wide margin the largest step
there -- roughly a fifth of the file -- and that file is past the 600-line
ceiling the architecture ratchet sets. Nothing about it is shared, which is what
makes it the clean cut: the account-binding rule it leans on *is* shared with
the schedule step, and lives in ``account_binding.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.domain.state import PlanStep
from app.modules.pod_bundle.infrastructure.account_binding import (
    validate_account_binding,
)


async def apply_surface(
    step: PlanStep,
    *,
    uow,
    ctx,
    pod_id: UUID,
    load: Callable[[str, str], dict[str, Any]],
) -> None:
    """Create or update a pod surface, binding the connector ``account_id``
    resolved from the required ``${..._account}`` variable. A pod may have
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

    payload = load("surfaces", step.name)
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
    # account_id has already been substituted from the provided account
    # variable by load.
    request = SurfaceCreateRequest.model_validate(
        {
            "platform": platform.value,
            "name": resolved_name,
            **{
                key: value
                for key, value in payload.items()
                if key
                in {
                    "default_agent_name",
                    "account_id",
                    "credential_mode",
                    "config",
                    "is_enabled",
                }
            },
        }
    )

    await validate_account_binding(
        uow,
        account_id=request.account_id,
        expected_connector=payload.get("connector_id"),
        expected_kind=payload.get("connector_kind") or payload.get("provider"),
        resource_label=f"Surface '{resolved_name}'",
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
            account_id=request.account_id,
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
        account_id=request.account_id,
        is_active=(
            request.is_enabled if "is_enabled" in request.model_fields_set else None
        ),
        ctx=ctx,
    )
