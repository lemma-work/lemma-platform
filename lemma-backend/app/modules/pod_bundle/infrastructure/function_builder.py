"""Self-scoped function bundle step runner.

Function compilation uses the sandbox runtime and object storage, so it must never run
inside the apply loop's shared database unit of work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionType,
    FunctionUpdateEntity,
)
from app.modules.pod_bundle.infrastructure.applier import _substitute


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
        await self._use_cases.upsert_function_for_import(
            entity=entity,
            update_entity=update,
            code=code,
            user_id=user_id,
        )

        # Grants are NOT applied here. They are a deferred FUNCTION_GRANTS plan
        # step (see plan_builder / applier._apply_function_grants) so a function
        # granted a folder, an app, or another function that this same bundle
        # creates resolves those names after they exist — this step runs before
        # agents, apps, and files.
