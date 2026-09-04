"""Exporting a pod's configured surfaces into a bundle's ``surfaces/``.

Split out of :mod:`exporter` on the same grounds as :mod:`exporter_files` and
:mod:`exporter_agents`: the module it came from is long enough that a reader
looking for one resource type has to skip past six others.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_surface_payload

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger

logger = get_logger(__name__)


async def export_surfaces(
    root: Path,
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    warnings: list[str],
) -> None:
    """Export configured surfaces best-effort: a surface that can't be
    serialized is skipped with a warning, never failing the whole export.

    Best-effort must still be audible (the lesson ``export_pod_files`` already
    learned): a bundle with no ``surfaces/`` looks identical to a pod that has
    none, and the person importing it only finds out when the surface never
    answers.
    """
    from app.modules.agent_surfaces.contracts.provisioning import (
        list_surfaces,
        surface_response,
    )
    from app.modules.connectors.contracts.provisioning import (
        resolve_account_connector,
    )
    from app.modules.pod_bundle.infrastructure.exporter import _dump_response

    try:
        surfaces = await list_surfaces(uow, pod_id=pod_id)
    except Exception as exc:  # noqa: BLE001 - surfaces are best-effort
        logger.debug(
            "pod_bundle.exporter.skipping_surface_export_pod_s.diagnostic",
            pod_id=pod_id,
        )
        warnings.append(
            f"surface export skipped: the pod's surfaces could not be listed "
            f"({type(exc).__name__})."
        )
        return

    seen_names: set[str] = set()
    for surface in surfaces:
        surface_label = str(getattr(surface, "name", None) or "unnamed")
        try:
            raw_surface = _dump_response(surface_response(surface))
            account_id = raw_surface.get("account_id")
            if account_id:
                info = await resolve_account_connector(uow, UUID(str(account_id)))
                if info is None:
                    raise ValueError(
                        f"Surface references account {account_id}, which no "
                        "longer exists."
                    )
                raw_surface["connector_id"], raw_surface["connector_kind"] = info
            payload = _normalize_surface_payload(raw_surface)
            platform = str(payload.get("platform") or "")
            # De-dup by the surface's pod-unique name (not platform), so a pod
            # with several surfaces of one platform exports all of them.
            surface_name = str(payload.get("name") or "")
            if not platform or not surface_name or surface_name in seen_names:
                continue
            seen_names.add(surface_name)
            dir_ = root / "surfaces" / surface_name
            dir_.mkdir(parents=True, exist_ok=True)
            _write_json(dir_ / f"{surface_name}.json", payload)
        except Exception as exc:  # noqa: BLE001 - one bad surface is not fatal
            logger.debug(
                "pod_bundle.exporter.skipping_surface_s_pod_s.diagnostic",
                pod_id=pod_id,
            )
            warnings.append(
                f"surface '{surface_label}' was skipped: it could not be "
                f"serialized ({type(exc).__name__})."
            )
