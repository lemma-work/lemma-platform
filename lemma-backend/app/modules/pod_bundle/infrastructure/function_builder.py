"""Self-scoped function bundle step runner.

Function compilation uses AgentBox and object storage, so it must never run
inside the apply loop's shared database unit of work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.core.authorization.grants import (
    normalize_pod_resource_grants,
    replace_grantee_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.scope import uow_scope
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionType,
    FunctionUpdateEntity,
)
from app.modules.pod_bundle.infrastructure.applier import (
    _grants_from_payload,
    _substitute,
)


class FunctionStepRunner:
    """Apply one function through the canonical short-UoW function saga."""

    def __init__(self, *, uow_factory: Any, use_cases: Any | None = None) -> None:
        self._uow_factory = uow_factory
        if use_cases is None:
            from app.modules.function.api.dependencies import (
                build_function_use_cases,
            )

            use_cases = build_function_use_cases(uow_factory)
        self._use_cases = use_cases

    async def run(
        self,
        step: Any,
        *,
        pod_id: UUID,
        user_id: UUID,
        bundle_root: Path,
        replacements: dict[str, str],
    ) -> None:
        resource_dir = bundle_root / "functions" / step.name
        payload = _substitute(
            load_resource_payload(
                resource_dir,
                step.name,
                resource_type="functions",
            ),
            replacements,
        )
        code_value = payload.get("code")
        code = code_value if isinstance(code_value, str) else None
        function_type = FunctionType(str(payload.get("type") or FunctionType.API.value))
        entity = FunctionEntity(
            pod_id=pod_id,
            user_id=user_id,
            name=step.name,
            description=payload.get("description"),
            icon_url=payload.get("icon_url"),
            config=payload.get("config"),
            type=function_type,
            visibility=payload.get("visibility") or "POD",
        )
        update = FunctionUpdateEntity(
            description=payload.get("description"),
            icon_url=payload.get("icon_url"),
            code=code,
            config=payload.get("config"),
            type=function_type,
            visibility=payload.get("visibility"),
        )
        function = await self._use_cases.upsert_function_for_import(
            entity=entity,
            update_entity=update,
            code=code,
            user_id=user_id,
        )

        grants = _grants_from_payload(payload)
        if grants and function.id is not None:
            validate_pod_resource_grant_permissions(grants)
            async with uow_scope(self._uow_factory) as uow:
                normalized = await normalize_pod_resource_grants(
                    uow.session,
                    pod_id=pod_id,
                    grants=grants,
                )
                await replace_grantee_resource_grants(
                    uow.session,
                    pod_id=pod_id,
                    grantee_type="FUNCTION",
                    grantee_id=function.id,
                    grants=normalized,
                    created_by_user_id=user_id,
                )

            # Cache invalidation is external I/O and therefore follows the UoW.
            from app.composition.pod_bundle_apps import (
                invalidate_function_workspace_env_cache,
            )

            await invalidate_function_workspace_env_cache(
                pod_id=pod_id,
                function_id=function.id,
            )
