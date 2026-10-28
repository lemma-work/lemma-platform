"""Plan a bundle's pod connectors against the importing pod and organization.

For each ``connectors/<slug>/<slug>.json`` the plan finds the org's
installations of the catalog connector, whether the importer could make one,
and -- SHARED -- which of the importer's own accounts they could share. It
chooses nothing on the importer's behalf beyond the org's default installation;
sharing a credential in particular is never defaulted.

Pure over a :class:`ConnectorLookup` port, like the rest of the plan builder:
production wires :class:`ServiceConnectorLookup` (the connectors module's
``contracts.bundles`` over the plan job's short UoW), and unit tests a fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle import (
    BundleConnector,
    load_bundle_connectors,
    referenced_connector_slugs,
)

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.connector_plan import (
    AccountOption,
    ConnectorPlan,
    ConnectorPlanStatus,
    DeclaredConnector,
    InstallationOption,
    declared_conflict,
)
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)


class ConnectorLookup(Protocol):
    """What planning a bundle's connectors needs to know."""

    async def declared(self) -> dict[str, DeclaredConnector]:
        """The importing pod's connectors, by slug."""
        ...

    async def installations(
        self, connector_ids: Sequence[str]
    ) -> dict[str, list[InstallationOption]]: ...

    async def own_accounts(
        self, auth_config_ids: Sequence[UUID]
    ) -> list[AccountOption]: ...

    async def may_install(self) -> bool:
        """Whether the importer holds ``org.installation.manage``."""
        ...

    async def installable(self, connector_id: str) -> bool:
        """Whether Lemma's own app installs it with nothing typed in."""
        ...


class NoConnectorLookup:
    """A pod and org with no connectors and no way to make one."""

    async def declared(self) -> dict[str, DeclaredConnector]:
        return {}

    async def installations(
        self, connector_ids: Sequence[str]
    ) -> dict[str, list[InstallationOption]]:
        del connector_ids
        return {}

    async def own_accounts(
        self, auth_config_ids: Sequence[UUID]
    ) -> list[AccountOption]:
        del auth_config_ids
        return []

    async def may_install(self) -> bool:
        return False

    async def installable(self, connector_id: str) -> bool:
        del connector_id
        return False


class ServiceConnectorLookup:
    """Production :class:`ConnectorLookup`, over the plan job's short UoW."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        ctx: Context,
        pod_id: UUID,
        user_id: UUID,
    ):
        self._uow = uow
        self._pod_id = pod_id
        self._user_id = user_id
        self._organization_id = ctx.organization_id

    async def declared(self) -> dict[str, DeclaredConnector]:
        return await declared_in_pod(self._uow, pod_id=self._pod_id)

    async def installations(
        self, connector_ids: Sequence[str]
    ) -> dict[str, list[InstallationOption]]:
        from app.modules.connectors.contracts.bundles import installations_of

        if self._organization_id is None:
            return {}
        found = await installations_of(
            self._uow,
            organization_id=self._organization_id,
            connector_ids=connector_ids,
        )
        return {
            connector_id: [
                InstallationOption(
                    auth_config_id=choice.auth_config_id,
                    name=choice.name,
                    is_default=choice.is_default,
                )
                for choice in choices
            ]
            for connector_id, choices in found.items()
        }

    async def own_accounts(
        self, auth_config_ids: Sequence[UUID]
    ) -> list[AccountOption]:
        from app.modules.connectors.contracts.bundles import own_accounts_on

        if self._organization_id is None:
            return []
        return [
            AccountOption(
                account_id=choice.account_id,
                auth_config_id=choice.auth_config_id,
                label=choice.label,
            )
            for choice in await own_accounts_on(
                self._uow,
                user_id=self._user_id,
                organization_id=self._organization_id,
                auth_config_ids=auth_config_ids,
            )
        ]

    async def may_install(self) -> bool:
        from app.modules.connectors.contracts.own_apps import may_manage_installs

        if self._organization_id is None:
            return False
        return await may_manage_installs(
            self._uow, user_id=self._user_id, organization_id=self._organization_id
        )

    async def installable(self, connector_id: str) -> bool:
        from app.modules.connectors.contracts.bundles import lemma_app_installable

        return await lemma_app_installable(self._uow, connector_id=connector_id)


async def declared_in_pod(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> dict[str, DeclaredConnector]:
    """The pod's connectors by slug, as planning and applying both compare them."""
    from app.modules.connectors.contracts.bundles import declared_pod_connectors

    return {
        slug: DeclaredConnector(connector=found.connector_id, mode=found.mode)
        for slug, found in (await declared_pod_connectors(uow, pod_id=pod_id)).items()
    }


def _read_connectors(bundle_root: Path) -> list[BundleConnector]:
    try:
        return load_bundle_connectors(bundle_root)
    except ValueError as exc:
        raise BundleInvalidError(str(exc)) from exc


def _missing_installation_message(connector: BundleConnector, can_create: bool) -> str:
    if can_create:
        return (
            f"Your organization has no {connector.connector} installation. Choose "
            f"to install Lemma's {connector.connector} app as part of this import, "
            "or install it on the Connectors page and plan again."
        )
    return (
        f"Your organization has no {connector.connector} installation, and "
        "installing one needs an organization admin (or its own app "
        "credentials). Ask an admin to install it on the Connectors page, then "
        "plan this import again."
    )


async def _plan_one(
    connector: BundleConnector,
    *,
    declared: dict[str, DeclaredConnector],
    installations: list[InstallationOption],
    lookup: ConnectorLookup,
    may_install: bool,
    used_by: list[str],
) -> ConnectorPlan:
    plan = ConnectorPlan(
        slug=connector.slug,
        connector=connector.connector,
        mode="SHARED" if connector.is_shared else "PER_MEMBER",
        required=connector.required,
        purpose=connector.purpose,
        visibility="RESTRICTED" if connector.visibility == "RESTRICTED" else "POD",
        status=ConnectorPlanStatus.READY,
        installations=installations,
        used_by=used_by,
    )
    existing = declared.get(connector.slug)
    if existing is not None:
        conflict = declared_conflict(
            connector.slug,
            connector=connector.connector,
            mode=plan.mode,
            declared=existing,
        )
        return plan.model_copy(
            update={
                "status": ConnectorPlanStatus.CONFLICT
                if conflict
                else ConnectorPlanStatus.EXISTS,
                "message": conflict
                or (
                    "This pod already declares it; the pod's own installation "
                    "and account are kept."
                ),
            }
        )
    can_create = (
        not installations
        and may_install
        and await lookup.installable(connector.connector)
    )
    default = installations[0].auth_config_id if installations else None
    update: dict[str, object] = {
        "auth_config_id": default,
        "can_create_installation": can_create,
    }
    if default is None:
        update["status"] = ConnectorPlanStatus.NEEDS_INSTALLATION
        update["message"] = _missing_installation_message(connector, can_create)
    elif connector.is_shared:
        update["account_options"] = await lookup.own_accounts(
            [option.auth_config_id for option in installations]
        )
        update["status"] = ConnectorPlanStatus.NEEDS_ACCOUNT
        update["message"] = (
            "Runs as one account for everyone. Share one of your own accounts "
            "for it, or it imports needing someone to share one."
        )
    elif connector.required:
        update["message"] = (
            "Each member uses their own account; members who have none are "
            "asked to connect one."
        )
    return plan.model_copy(update=update)


def _warning_for(plan: ConnectorPlan) -> str | None:
    label = f"Connector '{plan.slug}' ({plan.connector})"
    if plan.status is ConnectorPlanStatus.NEEDS_ACCOUNT:
        return (
            f"{label} is shared: unless you share one of your accounts for it, "
            "it imports needing an account, and what runs through it waits "
            "until someone shares one."
        )
    if plan.status is ConnectorPlanStatus.READY and plan.required:
        return (
            f"{label} is per member and required: members will be asked to "
            "connect their own."
        )
    return None


def _step_for(plan: ConnectorPlan) -> PlanStep:
    detail = {"connector": plan.connector, "mode": plan.mode}
    if plan.declares:
        return PlanStep(
            index=0,
            kind=StepKind.CONNECTOR,
            name=plan.slug,
            action=StepAction.CREATE,
            detail=detail,
        )
    if plan.status is ConnectorPlanStatus.EXISTS:
        # Writes nothing, but still runs: the apply checks the pod's own
        # declaration again, so one changed since the plan is refused there
        # for the reason the plan would have given.
        return PlanStep(
            index=0,
            kind=StepKind.CONNECTOR,
            name=plan.slug,
            action=StepAction.SKIP,
            detail=detail,
        )
    return PlanStep(
        index=0,
        kind=StepKind.CONNECTOR,
        name=plan.slug,
        action=StepAction.SKIP,
        status=StepStatus.SKIPPED,
        detail=detail,
        error=plan.message,
    )


def _unbound_reference_warnings(
    refs: dict[str, list[str]], declared: set[str]
) -> list[str]:
    """Resources naming a slug that neither the bundle nor the pod declares."""
    return [
        f"{', '.join(where)} name{'s' if len(where) == 1 else ''} connector "
        f"'{slug}', which neither this bundle nor the pod declares; "
        "declare it in the pod before using them."
        for slug, where in sorted(refs.items())
        if slug not in declared
    ]


async def plan_connectors(
    bundle_root: Path, lookup: ConnectorLookup
) -> tuple[list[ConnectorPlan], list[PlanStep], list[str]]:
    """The bundle's connectors planned: their plans, steps and warnings."""
    connectors = _read_connectors(bundle_root)
    refs = referenced_connector_slugs(bundle_root)
    declared = await lookup.declared()
    installations = (
        await lookup.installations(sorted({c.connector for c in connectors}))
        if connectors
        else {}
    )
    may_install = bool(connectors) and await lookup.may_install()
    plans = [
        await _plan_one(
            connector,
            declared=declared,
            installations=installations.get(connector.connector, []),
            lookup=lookup,
            may_install=may_install,
            used_by=refs.get(connector.slug, []),
        )
        for connector in connectors
    ]
    warnings = [w for plan in plans if (w := _warning_for(plan)) is not None]
    warnings.extend(
        _unbound_reference_warnings(
            refs, set(declared) | {connector.slug for connector in connectors}
        )
    )
    return plans, [_step_for(plan) for plan in plans], warnings


__all__ = [
    "ConnectorLookup",
    "NoConnectorLookup",
    "ServiceConnectorLookup",
    "declared_in_pod",
    "plan_connectors",
]
