"""Bundled grants on a connector the importing pod does not declare.

A grant names a pod connector by slug, and a pod connector is declared per pod.
A bundle from a pod whose connector this one never declared -- every bundle
exported before connectors were per pod -- names it by what the exporting pod
called it, and the grants step failed with "Unknown names" after the agents and
functions it grants to had already been written.

So the name is settled before anything is: planning finds the importing
organization's installation for each such name and refuses the whole import
when there is none (:func:`plan_connector_grants`). Applying then declares the
pod connector for that installation through the one helper every caller uses
(`ensure_pod_connector_for_installation`, PER_MEMBER: each person uses their
own account) and grants on the slug it answers with
(:func:`resolve_connector_grants`).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection
from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput,
    grants_from_payload,
    has_grants,
)

#: The grant holders whose grants name connectors, as ``(directory, label)``.
_HOLDERS = (("agents", "agent"), ("functions", "function"))
_CONNECTOR = "connector"


class ConnectorDirectory(Protocol):
    """What planning asks about the importing pod's connectors."""

    async def declares(self, slug: str) -> bool: ...
    async def installation_for(self, name: str) -> UUID | None: ...


def connector_grant_names(bundle_root: Path) -> dict[str, list[str]]:
    """Every connector a bundled agent or function is granted, by the name the
    bundle uses, with who holds a grant on it ("agent 'triage'")."""
    found: dict[str, list[str]] = {}
    for directory, label in _HOLDERS:
        type_dir = bundle_root / directory
        if not type_dir.is_dir():
            continue
        for resource_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
            payload = load_resource_payload(
                resource_dir, resource_dir.name, resource_type=directory
            )
            if not has_grants(payload):
                continue
            for grant in grants_from_payload(payload):
                if grant.resource_type.value == _CONNECTOR:
                    holders = found.setdefault(grant.resource_name, [])
                    holders.append(f"{label} '{resource_dir.name}'")
    return found


async def plan_connector_grants(
    bundle_root: Path,
    directory: ConnectorDirectory,
    *,
    bundled: Collection[str] = (),
) -> list[str]:
    """Refuse a bundle granting a connector nobody can declare; warn of the rest.

    Returns the plan's warnings: one per connector the import will declare.
    Raises before anything is written when a granted connector is neither
    declared by the pod nor installed in the organization, naming each. A
    connector the bundle declares itself (``bundled``, its ``connectors/``) is
    ``connector_planning``'s, which declares it before any grant names it.
    """
    warnings: list[str] = []
    unknown: list[str] = []
    for name, holders in sorted(connector_grant_names(bundle_root).items()):
        if name in bundled or await directory.declares(name):
            continue
        who = ", ".join(holders)
        if await directory.installation_for(name) is None:
            unknown.append(f"'{name}' (granted to {who})")
            continue
        warnings.append(
            f"Connector '{name}' is granted to {who}, and this pod does not "
            "declare it: importing declares it, with each member using their "
            "own account."
        )
    if unknown:
        raise BundleInvalidError(
            "This bundle grants connectors that this pod does not declare and "
            f"your organization has not installed: {'; '.join(unknown)}. "
            "Install them in the organization, or declare them in this pod, "
            "and import again."
        )
    return warnings


class ServiceConnectorDirectory:
    """Production :class:`ConnectorDirectory`, over the connectors contracts."""

    def __init__(
        self, *, uow: SqlAlchemyUnitOfWork, pod_id: UUID, organization_id: UUID | None
    ):
        self._uow = uow
        self._pod_id = pod_id
        self._organization_id = organization_id

    async def declares(self, slug: str) -> bool:
        from app.modules.connectors.contracts.pod_connectors import get_pod_connector

        found = await get_pod_connector(self._uow, pod_id=self._pod_id, slug=slug)
        return found is not None

    async def installation_for(self, name: str) -> UUID | None:
        from app.modules.connectors.contracts.provisioning import (
            installation_for_bundle_name,
        )

        if self._organization_id is None:
            return None
        return await installation_for_bundle_name(
            self._uow, organization_id=self._organization_id, name=name
        )


async def resolve_connector_grants(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    grants: list[GrantInput],
) -> list[GrantInput]:
    """``grants`` with each undeclared connector declared and named by its slug.

    A name with no installation is left as it is, and the grant write that
    follows refuses it by name; planning has already refused such a bundle, so
    reaching that means the installation went away between plan and apply.
    """
    if not any(g.resource_type.value == _CONNECTOR for g in grants):
        return grants
    from app.modules.connectors.contracts import pod_connectors

    directory = ServiceConnectorDirectory(
        uow=uow, pod_id=pod_id, organization_id=ctx.organization_id
    )
    slugs: dict[str, str] = {}
    resolved: list[GrantInput] = []
    for grant in grants:
        name = grant.resource_name
        if grant.resource_type.value != _CONNECTOR or await directory.declares(name):
            resolved.append(grant)
            continue
        if name not in slugs:
            installation = await directory.installation_for(name)
            if installation is None:
                resolved.append(grant)
                continue
            # Looked up on the module at call time, so a test can stand in
            # for it by its dotted path.
            declared = await pod_connectors.ensure_pod_connector_for_installation(
                uow, ctx, pod_id=pod_id, auth_config_id=installation
            )
            slugs[name] = declared.slug
        resolved.append(dataclasses.replace(grant, resource_name=slugs[name]))
    return resolved
