"""Approving a planned import: what is checked before anything is applied.

Two callers make the same choice in two places. A person applies an import
from its install page (``ImportUseCases.apply_import``), and a plugin install
applies each of its bundles with the page's defaults, in a worker, with nobody
on the page (``events/plugin_install``). Both go through :func:`approve_plan`,
so the queue can never apply something the page would have refused, and the
reason it gives for stopping is the one the page would have shown.

Everything here runs while the pod is still untouched: a refusal is a
:class:`DomainError` and nothing has been written.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    resolve_connector_choices,
)
from app.modules.pod_bundle.domain.errors import BundleConfirmationRequiredError
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    ImportState,
    ImportStatus,
    StepStatus,
)


async def approve_plan(
    uow_factory: UnitOfWorkFactory,
    state: ImportState,
    *,
    user_id: UUID,
    variables: dict[str, str] | None,
    confirm_destructive: bool,
    connectors: dict[str, ConnectorChoice] | None,
) -> None:
    """Check ``state``'s plan may be applied as chosen, and record the choices.

    Raises before changing ``state`` when it may not. Otherwise ``state`` is
    ready to save as ``APPLYING``: the choices are on it, its failed steps are
    pending again (steps already done stay done, so applying resumes), and the
    last error is cleared. Saving it is the caller's.
    """
    plan = state.plan
    assert plan is not None, "checked by the caller"
    if plan.has_destructive_steps and not confirm_destructive:
        raise BundleConfirmationRequiredError(
            "This import would drop or alter table columns. Re-submit with "
            "confirm_destructive=true to proceed.",
            details={"warnings": plan.warnings},
        )
    _require_variables(plan, variables)
    choices = _resolve_connector_choices(plan, connectors)
    # Grants the importer could not make themselves: refused here, with the
    # pod untouched, rather than by the last step of the apply. Imported
    # here: only an apply needs it, not every process serving the routes.
    from app.modules.pod_bundle.infrastructure.grant_planning import (
        refuse_unconferrable_grants,
    )

    refuse_unconferrable_grants(plan)
    state.connector_choices = choices
    await _check_accounts(uow_factory, state, user_id=user_id, variables=variables)

    for step in plan.steps:
        if step.status == StepStatus.FAILED:
            step.status = StepStatus.PENDING
            step.error = None
    state.variables_provided = dict(variables or {})
    state.confirm_destructive = confirm_destructive
    state.status = ImportStatus.APPLYING
    state.error = None
    state.error_type = None
    state.error_code = None
    state.completed_at = None


def _require_variables(plan: ImportPlan, variables: dict[str, str] | None) -> None:
    missing = [
        item.name
        for item in plan.variables
        if item.required and not (variables or {}).get(item.name)
    ]
    if missing:
        raise BundleConfirmationRequiredError(
            "Required variables are missing.", details={"missing": missing}
        )


def _resolve_connector_choices(
    plan: ImportPlan, requested: dict[str, ConnectorChoice] | None
) -> dict[str, ConnectorChoice]:
    """The importer's connector choices made whole, or why the apply is held.

    Checked before anything is queued, so a connector that cannot be declared
    stops the import while the pod is still untouched -- rather than failing
    at its step after nothing, or worse after half the pod, landed."""
    resolved, blocking = resolve_connector_choices(plan.connectors, requested or {})
    if blocking:
        raise BundleConfirmationRequiredError(
            "Some of this bundle's connectors cannot be declared yet: "
            + "; ".join(f"'{b.slug}': {b.reason}" for b in blocking)
            + ".",
            details={"blocking": [b.model_dump() for b in blocking]},
        )
    return resolved


async def _check_accounts(
    uow_factory: UnitOfWorkFactory,
    state: ImportState,
    *,
    user_id: UUID,
    variables: dict[str, str] | None,
) -> None:
    """The accounts this apply would use, checked while the pod is still
    untouched (``infrastructure/account_checks``): a narrow share is refused,
    and a schedule naming an account the importer cannot use is planned as
    skipped now that its import variable is known."""
    from app.modules.pod_bundle.infrastructure.account_checks import (
        ServiceAccountUsability,
        refuse_narrow_shares,
        skip_unusable_accounts,
        steps_naming_an_account,
    )

    plan = state.plan
    assert plan is not None, "checked by the caller"
    shares = {
        slug: choice.share_account_id
        for slug, choice in state.connector_choices.items()
        if choice.share_account_id is not None
    }
    if not shares and not steps_naming_an_account(plan.steps):
        return
    replacements = {v.name: v.default for v in plan.variables if v.default is not None}
    replacements.update(variables or {})
    async with uow_scope(uow_factory) as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=state.pod_id
        )
        async with context_scope(ctx):
            await refuse_narrow_shares(uow, pod_id=state.pod_id, shares=shares)
            await skip_unusable_accounts(
                plan.steps,
                state.warnings,
                replacements=replacements,
                check=ServiceAccountUsability(uow=uow, ctx=ctx, pod_id=state.pod_id),
            )


__all__ = ["approve_plan"]
