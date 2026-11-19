"""Self-scoped function bundle step runner.

Function compilation uses the sandbox runtime and object storage, so it must never run
inside the apply loop's shared database unit of work.

A function the pod already has is kept as it is: its code, schema and access
stay the pod's. The plan keeps one it saw (``kept_parts``); this runner keeps
one that is this import's own from before a crash, or arrived since. A function
is written in the saga's last unit of work, so one that exists is whole.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.modules.function.contracts import (
    FunctionEntity,
    FunctionType,
    FunctionUpdateEntity,
)
from app.modules.pod_bundle.infrastructure.applier import _substitute


class FunctionStepRunner:
    """Apply one function through the canonical short-UoW function saga."""

    def __init__(
        self,
        *,
        uow_factory: Any,
        use_cases: Any | None = None,
        exists: Callable[[str, UUID, UUID], Awaitable[bool]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        if use_cases is None:
            from app.modules.function.api.dependencies import (
                build_function_use_cases,
            )

            use_cases = build_function_use_cases(uow_factory)
        self._use_cases = use_cases
        self._exists = exists or self._function_exists

    async def run(
        self,
        step: Any,
        *,
        pod_id: UUID,
        user_id: UUID,
        bundle_root: Path,
        replacements: dict[str, str],
    ) -> None:
        if await self._exists(step.name, pod_id, user_id):
            return
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

    async def _function_exists(self, name: str, pod_id: UUID, user_id: UUID) -> bool:
        """Whether the pod has this function, asked as the importer in a short
        unit of work of its own."""
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService
        from app.modules.function.contracts.provisioning import get_function

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                found = await get_function(
                    uow,
                    pod_id=pod_id,
                    name=name,
                    user_id=user_id,
                    ctx=ctx,
                    include_code=False,
                )
        return found is not None
