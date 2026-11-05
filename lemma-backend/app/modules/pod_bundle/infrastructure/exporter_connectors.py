"""Exporting a pod's connectors into a bundle's ``connectors/``.

Each pod connector becomes ``connectors/<slug>/<slug>.json`` carrying its
catalog connector, mode, ``required`` and purpose -- and nothing that names the
organization it is leaving: no installation, no shared account. Those are the
importer's to choose in their own org, and a bundle that carried them would
name ids that mean nothing there (or, worse, something). The schedules,
surfaces and grants that run through a connector already name it by slug.

Not best-effort, unlike surfaces: a bundle missing a connector its schedules
and agents name is a bundle that imports bound to nothing, so a failure to read
them fails the export.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_pod_connector_payload

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def export_connectors(
    root: Path, uow: SqlAlchemyUnitOfWork, *, ctx: Context, pod_id: UUID
) -> None:
    from app.modules.connectors.contracts.bundles import exported_pod_connectors

    for raw in await exported_pod_connectors(uow, ctx, pod_id=pod_id):
        slug = str(raw.get("slug") or "")
        if not slug:
            continue
        resource_dir = root / "connectors" / slug
        resource_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            resource_dir / f"{slug}.json", _normalize_pod_connector_payload(raw)
        )
