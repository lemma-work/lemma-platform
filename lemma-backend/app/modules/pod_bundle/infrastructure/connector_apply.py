"""Apply one CONNECTOR step: declare a bundled pod connector in the pod.

Runs first, so every schedule, surface, agent and function that names the slug
finds it. Idempotent like every applier: a slug the pod already declares for
the same catalog connector in the same mode -- because a replay after a crash
got here before, or someone declared it since the plan -- is left as the pod
has it. One that disagrees is refused, with the reason the plan gives for the
same case (``connector_plan.declared_conflict``): the two are equally strict.

The installation and the shared account come from the importer's choices,
checked against the plan when the apply was requested
(``connector_plan.resolve_connector_choices``); the bundle names neither.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from lemma_pod_bundle import load_resource_payload, read_bundle_connector

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    declared_conflict,
)
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    PodBundleDomainError,
)
from app.modules.pod_bundle.domain.state import PlanStep


async def apply_connector(
    step: PlanStep,
    *,
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    pod_id: UUID,
    user_id: UUID,
    bundle_root: Path,
    choices: Mapping[str, ConnectorChoice],
    warnings: list[str],
) -> None:
    from app.modules.connectors.contracts.bundles import (
        declare_bundled_pod_connector,
        ensure_lemma_app_installation,
    )
    from app.modules.pod_bundle.infrastructure.connector_planning import (
        declared_in_pod,
    )

    resource_dir = bundle_root / "connectors" / step.name
    try:
        connector = read_bundle_connector(
            step.name, load_resource_payload(resource_dir, step.name)
        )
    except ValueError as exc:
        raise BundleInvalidError(str(exc)) from exc
    existing = (await declared_in_pod(uow, pod_id=pod_id)).get(step.name)
    if existing is not None:
        conflict = declared_conflict(
            connector.slug,
            connector=connector.connector,
            mode=connector.mode,
            declared=existing,
        )
        if conflict:
            raise PodBundleDomainError(
                conflict,
                code="POD_BUNDLE_CONNECTOR_CONFLICT",
                status_code=409,
            )
        return
    choice = choices.get(step.name)
    if choice is None:
        raise PodBundleDomainError(
            f"No installation was chosen for connector '{step.name}'. Apply the "
            "import again to choose one.",
            code="POD_BUNDLE_CONNECTOR_UNCHOSEN",
            status_code=422,
        )
    auth_config_id = choice.auth_config_id
    if choice.create_installation:
        if ctx.organization_id is None:
            raise PodBundleDomainError(
                "This pod belongs to no organization to install a connector in.",
                code="POD_BUNDLE_CONNECTOR_UNCHOSEN",
                status_code=422,
            )
        auth_config_id = await ensure_lemma_app_installation(
            uow,
            user_id=user_id,
            organization_id=ctx.organization_id,
            connector_id=connector.connector,
        )
    assert auth_config_id is not None, "resolved choices always name one"
    status = await declare_bundled_pod_connector(
        uow,
        ctx,
        pod_id=pod_id,
        slug=connector.slug,
        auth_config_id=auth_config_id,
        mode=connector.mode,
        required=connector.required,
        purpose=connector.purpose,
        visibility=connector.visibility,
        share_account_id=choice.share_account_id,
    )
    if status == "NEEDS_ACCOUNT":
        warnings.append(
            f"Connector '{connector.slug}' ({connector.connector}) was imported "
            "needing an account: it is shared, and no account was shared for it. "
            "Anyone with an account on it can share one from the pod's "
            "Connections page."
        )
