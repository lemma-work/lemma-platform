"""A template's import, staged the way an uploaded bundle's is.

Both ways a template arrives start here: an import into a pod that exists
(``kind=TEMPLATE``) and a Lemma started from one (``start_use_cases``). The
archive is packed from the template that ships with the server and put where
an upload would land, and the import's state names the template it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.concurrency.offload import run_blocking
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.templates import pack_template


@dataclass(frozen=True, slots=True)
class StagedTemplate:
    import_id: UUID
    template: str
    staging_key: str

    def import_state(self, *, pod_id: UUID, user_id: UUID) -> ImportState:
        """The import, queued and already staged: it goes straight to planning."""
        return ImportState(
            import_id=self.import_id,
            pod_id=pod_id,
            user_id=user_id,
            status=ImportStatus.QUEUED,
            staging_key=self.staging_key,
            source=BundleSource(
                kind=BundleSourceKind.TEMPLATE,
                template=self.template,
                url=f"template:{self.template}",
            ),
        )


async def stage_template(
    staging: BundleStagingStorage, template: str, import_id: UUID
) -> StagedTemplate:
    """Pack ``template`` and stage it for ``import_id``; 404 for an unknown name.

    External I/O: call it with no transaction holding anything open.
    """
    archive = await run_blocking(pack_template, template)
    key = await staging.put_archive("pod-imports", import_id, archive)
    return StagedTemplate(import_id=import_id, template=template, staging_key=key)


__all__ = ["StagedTemplate", "stage_template"]
