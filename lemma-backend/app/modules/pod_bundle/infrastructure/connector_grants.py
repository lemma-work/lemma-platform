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

A grant on a bundled connector the import leaves out -- optional, and the
organization has nothing to map it to -- cannot be written: there is no pod
connector to name. It waits instead (`hold_grants_until_declared`), and is
granted on the connector the pod later declares for that catalog connector,
on somebody's first use or by hand. One left out while an installation exists
was left out by choice, and its grant is dropped as the importer was told.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from lemma_pod_bundle import (
    BundleConnector,
    load_bundle_connectors,
    load_resource_payload,
)

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    skipped_connectors,
    waiting_for,
)
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput,
    grants_from_payload,
    has_grants,
)

if TYPE_CHECKING:
    from app.modules.connectors.contracts.bundles import WaitingConnectorGrant

#: The grant holders whose grants name connectors, as ``(directory, label)``.
_HOLDERS = (("agents", "agent"), ("functions", "function"))
_CONNECTOR = "connector"


class HoldGrants(Protocol):
    """`connectors.contracts.bundles.hold_grants_until_declared`'s shape."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        ctx: Context,
        *,
        pod_id: UUID,
        grantee_type: str,
        grantee_id: UUID,
        grants: Sequence[WaitingConnectorGrant],
        created_by_user_id: UUID | None,
    ) -> bool: ...


class ConnectorDirectory(Protocol):
    """What planning asks about the importing pod's connectors."""

    async def declares(self, slug: str) -> bool: ...
    async def installation_for(self, name: str) -> UUID | None: ...


def connector_grant_names(
    bundle_root: Path, writing: Collection[tuple[str, str]] | None = None
) -> dict[str, list[str]]:
    """Every connector a bundled agent or function is granted, by the name the
    bundle uses, with who holds a grant on it ("agent 'triage'").

    ``writing`` narrows it to the holders whose grants the import writes, as
    ``(directory, name)``: one the pod already has keeps its own access, so
    what its bundled copy was granted names nothing the import must declare."""
    found: dict[str, list[str]] = {}
    for directory, label in _HOLDERS:
        type_dir = bundle_root / directory
        if not type_dir.is_dir():
            continue
        for resource_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
            if writing is not None and (directory, resource_dir.name) not in writing:
                continue
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
    writing: Collection[tuple[str, str]] | None = None,
) -> list[str]:
    """Refuse a bundle granting a connector nobody can declare; warn of the rest.

    Returns the plan's warnings: one per connector the import will declare.
    Raises before anything is written when a granted connector is neither
    declared by the pod nor installed in the organization, naming each. A
    connector the bundle declares itself (``bundled``, its ``connectors/``) is
    ``connector_planning``'s, which declares it before any grant names it.
    Only the grants the import writes count (``writing``, as in
    :func:`connector_grant_names`).
    """
    warnings: list[str] = []
    unknown: list[str] = []
    granted = connector_grant_names(bundle_root, writing)
    for name, holders in sorted(granted.items()):
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
    choices: Mapping[str, ConnectorChoice] | None = None,
    warnings: list[str] | None = None,
    bundle_root: Path | None = None,
    waiting: list[WaitingConnectorGrant] | None = None,
) -> list[GrantInput]:
    """``grants`` with each undeclared connector declared and named by its slug.

    A name with no installation is left as it is, and the grant write that
    follows refuses it by name; planning has already refused such a bundle, so
    reaching that means the installation went away between plan and apply.

    A grant on a bundled connector the importer's ``choices`` leave out
    (optional, nothing to map it to) is dropped and said in ``warnings``: the holder is
    imported without it rather than the whole grant write refusing a name the
    importer was told would be missing. Unless the pod declares that slug after
    all -- by hand, since the plan -- when the grant stands. Given ``waiting``
    and the ``bundle_root`` that says what the connector is, a grant left out
    for want of any installation goes into ``waiting`` rather than away.
    """
    if not any(g.resource_type.value == _CONNECTOR for g in grants):
        return grants
    from app.modules.connectors.contracts import pod_connectors

    left_out = skipped_connectors(choices or {})

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
        if name in left_out:
            waits = (
                await _waiting_grant(directory, bundle_root, grant)
                if waiting is not None
                else None
            )
            if waits is not None and waiting is not None:
                waiting.append(waits)
                note = (
                    f"A grant on connector '{name}' waits for it: it was left "
                    "out, and is granted once this pod has "
                    f"{waiting_for(waits.connector, waits.mode)}."
                )
            else:
                note = _dropped_note(name)
            if warnings is not None and note not in warnings:
                warnings.append(note)
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


async def _waiting_grant(
    directory: ConnectorDirectory, bundle_root: Path | None, grant: GrantInput
) -> WaitingConnectorGrant | None:
    """``grant`` kept to wait for its connector, or ``None`` to drop it: when
    the organization has an installation of it, leaving it out was a choice."""
    from app.modules.connectors.contracts.bundles import WaitingConnectorGrant

    bundled = _bundled_connector(bundle_root, grant.resource_name)
    if bundled is None or await directory.installation_for(bundled.connector):
        return None
    return WaitingConnectorGrant(
        connector=bundled.connector,
        mode=bundled.mode,
        permission_ids=tuple(grant.permission_ids),
        slug=grant.resource_name,
    )


def _bundled_connector(bundle_root: Path | None, slug: str) -> BundleConnector | None:
    if bundle_root is None:
        return None
    return next(
        (c for c in load_bundle_connectors(bundle_root) if c.slug == slug), None
    )


def _dropped_note(slug: str) -> str:
    return f"A grant on connector '{slug}' was dropped: it was left out."


async def lock_waiting_grants(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, grantee_type: str, grantee_id: UUID
) -> None:
    """Before a grantee's grants are replaced: lock what it waits for, so a
    connector declared meanwhile settles it only after the step commits.

    The session is marked as holding the locks (`transaction_locks`): an
    authorization check between here and the hold must not commit early and
    let them go. The step's commit clears the mark."""
    from app.core.authorization.context import ResourceType
    from app.core.authorization.pending_grants import lock_grantee_pending_keys
    from app.core.infrastructure.db.transaction_locks import (
        mark_transaction_scoped_lock,
    )

    mark_transaction_scoped_lock(uow.session)
    await lock_grantee_pending_keys(
        uow.session,
        pod_id=pod_id,
        resource_type=ResourceType.CONNECTOR,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
    )


async def hold_waiting_grants(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    waiting: list[WaitingConnectorGrant],
    created_by_user_id: UUID | None,
    warnings: list[str],
    hold: HoldGrants | None = None,
) -> None:
    """After a grantee's grants are replaced -- the replace would wipe a grant
    this settles onto a connector declared since resolving: what it now waits
    for, which replaces what it waited for before -- an empty set included, so a step
    run again clears what its first attempt held. Where nothing can be held
    (before migration 0078) the importer is told the grant was dropped."""
    if hold is None:
        from app.modules.connectors.contracts.bundles import (
            hold_grants_until_declared as hold,
        )

    held = await hold(
        uow,
        ctx,
        pod_id=pod_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        grants=waiting,
        created_by_user_id=created_by_user_id,
    )
    if held:
        return
    for grant in waiting:
        promise = f"A grant on connector '{grant.slug}' waits for it"
        warnings[:] = [w for w in warnings if not w.startswith(promise)]
        if (dropped := _dropped_note(grant.slug)) not in warnings:
            warnings.append(dropped)
