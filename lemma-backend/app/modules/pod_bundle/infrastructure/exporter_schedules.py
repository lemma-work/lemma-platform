"""Exporting a pod's schedules into a bundle's ``schedules/`` directory.

Split out of :mod:`exporter` on the same grounds as :mod:`exporter_agents` and
:mod:`exporter_surfaces`, and to make room there for deciders: that module is
past the 600-line ceiling and the architecture ratchet holds it where it is.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_schedule_payload

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def export_schedules(
    uow: SqlAlchemyUnitOfWork, *, root: Path, pod_id: UUID, ctx: Context
) -> None:
    """Write one directory per schedule, its account named by its connector."""
    # Imported here rather than at module load: `exporter` imports this module,
    # so naming it at the top would be a cycle.
    from app.modules.connectors.contracts.provisioning import (
        resolve_account_connector,
    )
    from app.modules.pod_bundle.infrastructure.exporter import _schedule_response_dict
    from app.modules.schedule.contracts.provisioning import list_schedules

    schedules = await list_schedules(uow, pod_id=pod_id, ctx=ctx)
    for schedule in sorted(schedules, key=lambda s: str(s.name or s.id or "")):
        schedule_name = str(schedule.name or schedule.id or "")
        dir_ = root / "schedules" / schedule_name
        dir_.mkdir(parents=True, exist_ok=True)
        raw_schedule = _schedule_response_dict(schedule)
        account_id = raw_schedule.get("account_id")
        if account_id:
            info = await resolve_account_connector(uow, UUID(str(account_id)))
            if info is None:
                from app.modules.pod_bundle.domain.errors import BundleInvalidError

                raise BundleInvalidError(
                    f"Schedule '{schedule_name}' references account "
                    f"{account_id}, which no longer exists."
                )
            raw_schedule["connector_id"], raw_schedule["connector_kind"] = info
        payload = _normalize_schedule_payload(raw_schedule)
        payload.setdefault("name", schedule_name)
        _write_json(dir_ / f"{schedule_name}.json", payload)
