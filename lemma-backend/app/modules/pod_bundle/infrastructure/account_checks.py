"""Accounts an import would use, checked before anything is applied.

Two of an import's steps can reach an account, and both used to find out
whether they could only at their own step -- after earlier steps had landed:

- a bundled SHARED connector the importer chose to share an account for. One
  they already share with only some people here would have to be widened to
  the whole pod, which the import refuses (``AccountSharedWithSomeError``), as
  lemma-cli's import of the same bundle does;
- a schedule exported before pod connectors, which names an account rather
  than a pod connector. One the importer could not use -- somebody else's,
  not shared with them here, or gone -- failed the import at the schedule's
  step. It is planned as skipped instead, with a warning, as soon as the
  account is known: at planning for an id written into the bundle, when the
  apply is requested for one given as an import variable.

Either way the pod is untouched when the import learns it cannot go ahead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)

#: The ``PlanStep.detail`` key a schedule's deprecated account is kept under:
#: the bundle's value as written (an id, or a ``${variable}``).
DEPRECATED_ACCOUNT_DETAIL = "deprecated_account"

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class AccountUsability(Protocol):
    """Whether the importer could use an account a bundle names."""

    async def refusal(self, account_id: UUID) -> str | None:
        """Why they could not, or None when they could."""
        ...


class AnyAccountUsable:
    """Asks nothing: every account is taken as usable, and the step decides."""

    async def refusal(self, account_id: UUID) -> str | None:
        del account_id
        return None


class ServiceAccountUsability:
    """Production :class:`AccountUsability`, over a short UoW and the importer's
    context (``connectors.contracts.bundles.deprecated_account_refusal``)."""

    def __init__(self, *, uow: SqlAlchemyUnitOfWork, ctx: Context, pod_id: UUID):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id

    async def refusal(self, account_id: UUID) -> str | None:
        from app.modules.connectors.contracts.bundles import (
            deprecated_account_refusal,
        )

        return await deprecated_account_refusal(
            self._uow, self._ctx, pod_id=self._pod_id, account_id=account_id
        )


def deprecated_account_ref(payload: Mapping[str, object]) -> str | None:
    """The account a schedule names instead of a pod connector, as written."""
    if "account_id" not in payload or payload.get("pod_connector"):
        return None
    raw = payload.get("account_id")
    return str(raw) if raw else None


def noting_deprecated_account(
    step: PlanStep, payload: Mapping[str, object]
) -> PlanStep:
    """``step``, noting the deprecated account its schedule names, if it is to
    be created: the account is checked once it is known."""
    ref = deprecated_account_ref(payload)
    if ref is not None and step.action is StepAction.CREATE:
        step.detail = {**step.detail, DEPRECATED_ACCOUNT_DETAIL: ref}
    return step


def _resolved(ref: str, replacements: Mapping[str, str]) -> UUID | None:
    value = _VARIABLE.sub(lambda m: replacements.get(m.group(1), m.group(0)), ref)
    try:
        return UUID(value.strip())
    except ValueError:
        # Unset or not an id: the schedule step leaves the schedule out and
        # says so, without asking for any account.
        return None


def steps_naming_an_account(steps: Sequence[PlanStep]) -> list[PlanStep]:
    """The schedule steps still to run that name a deprecated account."""
    return [
        step
        for step in steps
        if step.kind is StepKind.SCHEDULE
        and step.status is StepStatus.PENDING
        and step.detail.get(DEPRECATED_ACCOUNT_DETAIL)
    ]


async def skip_unusable_accounts(
    steps: Sequence[PlanStep],
    warnings: list[str],
    *,
    replacements: Mapping[str, str],
    check: AccountUsability,
) -> None:
    """Plan as skipped each schedule naming an account the importer cannot use."""
    for step in steps_naming_an_account(steps):
        account_id = _resolved(
            str(step.detail[DEPRECATED_ACCOUNT_DETAIL]), replacements
        )
        if account_id is None:
            continue
        reason = await check.refusal(account_id)
        if reason is None:
            continue
        step.action = StepAction.SKIP
        step.status = StepStatus.SKIPPED
        step.error = f"names account {account_id}, which you cannot use: {reason}"
        warnings.append(
            f"Schedule '{step.name}' names account {account_id} (deprecated), "
            f"which you cannot use here ({reason}); it will not be imported. "
            "Create it afterwards with the pod connector it should listen "
            "through."
        )


async def refuse_narrow_shares(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, shares: Mapping[str, UUID]
) -> None:
    """Refuse sharing, for a bundled connector, an account the pod holds
    shared with only some people (``AccountSharedWithSomeError``)."""
    from app.modules.connectors.contracts.bundles import (
        AccountSharedWithSomeError,
        restricted_pod_accounts,
    )

    if not shares:
        return
    narrow = await restricted_pod_accounts(
        uow, pod_id=pod_id, account_ids=list(shares.values())
    )
    for slug, account_id in sorted(shares.items()):
        if account_id in narrow:
            raise AccountSharedWithSomeError(slug=slug, account_id=account_id)


__all__ = [
    "DEPRECATED_ACCOUNT_DETAIL",
    "AccountUsability",
    "AnyAccountUsable",
    "ServiceAccountUsability",
    "deprecated_account_ref",
    "noting_deprecated_account",
    "refuse_narrow_shares",
    "skip_unusable_accounts",
    "steps_naming_an_account",
]
