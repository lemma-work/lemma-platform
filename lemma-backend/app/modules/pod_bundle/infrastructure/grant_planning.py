"""Planning a bundle's agent and function grants: refusing what the importer can't make.

An agent's or a function's grants are what it may do on its own. Setting them
is conferral (PS-ACCESS-010): whoever sets them must be able to do each one
themselves, on that very resource. An import sets them on the importer's
behalf, so the importer is asked -- while the plan is built, not when the
AGENT_GRANTS or FUNCTION_GRANTS step runs at the end of the apply, by which
point every table, agent and function in the bundle has already landed and the
import would fail with the pod half-changed.

The plan asks the same question the apply step asks (``apply_grants`` through
``replace_grantee_resource_grants``, which bounds the write by the importer's
context with ``assert_can_confer_replacement``), so the two are equally strict:

only what the write *adds* is conferred -- a grant the same-named agent or
function in the pod already holds is not held against the importer.

What the plan cannot see is what does not exist yet: what the bundle creates,
and what the apply creates beside it (an agent's ``/memory`` folder). Those are
the importer's own creations by the time the step runs, and the step asks
again. Refusing them here would make the plan stricter than the apply. A
connector the pod does not declare is ``connector_grants``' to plan.

A refused grant is kept on its step (``detail["refused"]``) and repeated in the
plan's warnings, so the person reviewing the import sees it; applying the plan
is refused with ``CONFERRAL_EXCEEDS_HOLDER`` until the bundle or the importer
changes (:func:`refuse_unconferrable_grants`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.errors import BundleGrantsRefusedError
from app.modules.pod_bundle.domain.state import ImportPlan, PlanStep, StepKind
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput,
    grants_from_payload,
)

if TYPE_CHECKING:
    from app.core.authorization.grants import NormalizedResourceGrant

#: The grant steps whose holder's manifest lives in ``<directory>/<name>``.
_HOLDERS = {
    StepKind.AGENT_GRANTS: ("agents", "AGENT"),
    StepKind.FUNCTION_GRANTS: ("functions", "FUNCTION"),
}


class GrantConferral(Protocol):
    """Which of a holder's grants the importer could not make themselves."""

    async def refusals(
        self,
        grants: Sequence[GrantInput],
        *,
        grantee_type: str,
        grantee_name: str,
    ) -> list[str]: ...


class NoGrantConferral:
    """Refuses nothing: the plan builder's default where there is no importer
    to ask (unit tests of the diff). The apply step still asks."""

    async def refusals(
        self,
        grants: Sequence[GrantInput],
        *,
        grantee_type: str,
        grantee_name: str,
    ) -> list[str]:
        del grants, grantee_type, grantee_name
        return []


class ServiceGrantConferral:
    """Production :class:`GrantConferral`: the conferral bound itself, asked as
    the importer, over the plan job's short unit of work."""

    def __init__(self, *, uow: SqlAlchemyUnitOfWork, ctx: Context, pod_id: UUID):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id

    async def refusals(
        self,
        grants: Sequence[GrantInput],
        *,
        grantee_type: str,
        grantee_name: str,
    ) -> list[str]:
        from app.core.authorization.context import ResourceType
        from app.core.authorization.grants import (
            NormalizedResourceGrant,
            validate_pod_resource_grant_permissions,
        )
        from app.core.authorization.resource_names import resolve_resource_id_by_name
        from app.core.domain.errors import BadRequestError

        session = self._uow.session
        # The same-named agent or function the import updates, if the pod has
        # one: what it already holds is not conferred again.
        grantee_id = await resolve_resource_id_by_name(
            session,
            pod_id=self._pod_id,
            resource_type=ResourceType(grantee_type.lower()),
            resource_name=grantee_name,
        )
        refused: list[str] = []
        for grant in grants:
            target = f"{grant.resource_type.value} '{grant.resource_name}'"
            try:
                validate_pod_resource_grant_permissions([grant])
            except BadRequestError as exc:
                refused.append(f"{target}: {exc.message}")
                continue
            resource_id = await resolve_resource_id_by_name(
                session,
                pod_id=self._pod_id,
                resource_type=grant.resource_type,
                resource_name=grant.resource_name,
            )
            if resource_id is None:
                # Created by this import; the step asks when it exists.
                continue
            missing = await self._not_conferrable(
                NormalizedResourceGrant(
                    resource_type=grant.resource_type,
                    resource_id=resource_id,
                    permission_ids=list(grant.permission_ids),
                ),
                grantee_type=grantee_type,
                grantee_id=grantee_id,
            )
            if missing:
                refused.append(f"{', '.join(missing)} on {target}")
        return refused

    async def _not_conferrable(
        self,
        grant: NormalizedResourceGrant,
        *,
        grantee_type: str,
        grantee_id: UUID | None,
    ) -> list[str]:
        """The permissions of ``grant`` the importer may not hand out, if any."""
        from app.core.authorization.conferral import (
            CONFERRAL_EXCEEDS_HOLDER,
            assert_can_confer_grants,
            assert_can_confer_replacement,
        )
        from app.core.domain.errors import DomainError

        action = f"grant a bundled {grantee_type.lower()} what you could not use"
        try:
            if grantee_id is None:
                await assert_can_confer_grants(
                    self._ctx, [grant], pod_id=self._pod_id, action=action
                )
            else:
                await assert_can_confer_replacement(
                    self._ctx,
                    self._uow.session,
                    [grant],
                    pod_id=self._pod_id,
                    grantee_type=grantee_type,
                    grantee_id=grantee_id,
                    action=action,
                )
        except DomainError as exc:
            if exc.code != CONFERRAL_EXCEEDS_HOLDER:
                raise
            details = exc.details if isinstance(exc.details, dict) else {}
            return list(details.get("permission_ids") or grant.permission_ids)
        return []


async def plan_grant_conferral(
    bundle_root: Path, steps: Sequence[PlanStep], conferral: GrantConferral
) -> list[str]:
    """Mark each agent and function grants step the importer could not make.

    Returns the plan's warnings, one per refused holder; the refusals are on
    the steps (``detail["refused"]``) for :func:`refuse_unconferrable_grants`.
    """
    warnings: list[str] = []
    for step in steps:
        holder = _HOLDERS.get(step.kind)
        if holder is None:
            continue
        directory, grantee_type = holder
        resource_dir = bundle_root / directory / step.name
        payload = load_resource_payload(
            resource_dir, step.name, resource_type=directory
        )
        refused = await conferral.refusals(
            grants_from_payload(payload),
            grantee_type=grantee_type,
            grantee_name=step.name,
        )
        if not refused:
            continue
        label = grantee_type.capitalize()
        step.detail = {**step.detail, "refused": refused}
        step.error = (
            f"{label} '{step.name}' would be granted what you could not use "
            f"yourself: {'; '.join(refused)}."
        )
        warnings.append(
            f"{step.error} Applying this import is refused until someone who "
            "holds them imports it, or the bundle drops those grants."
        )
    return warnings


def refuse_unconferrable_grants(plan: ImportPlan) -> None:
    """Refuse the apply while the plan holds a grant the importer can't make."""
    refused = plan.grant_refusals
    if refused:
        raise BundleGrantsRefusedError(refused)


__all__ = [
    "GrantConferral",
    "NoGrantConferral",
    "ServiceGrantConferral",
    "plan_grant_conferral",
    "refuse_unconferrable_grants",
]
