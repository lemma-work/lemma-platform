"""How many of a pod's files are on their way to being searchable, and how many failed.

Split out of ``file_repository.py`` to keep that module under the architecture
ratchet's size limit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.infrastructure.models import DatastoreFile


class DatastoreFileStatusCountsMixin:
    """Per-pod processing counts for the host repository's session."""

    session: object

    async def count_active_for_pod(self, pod_id: UUID) -> int:
        """Files this pod currently has queued or mid-flight (PENDING+PROCESSING).

        What the pod's indexing summary reports as "still on its way".
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(DatastoreFile)
                .where(
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.kind == "FILE",
                    DatastoreFile.search_enabled == True,  # noqa: E712
                    DatastoreFile.status.in_(
                        (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
                    ),
                )
            )
            or 0
        )

    async def count_failed_for_pod(self, pod_id: UUID) -> int:
        """Files this pod tried to index and could not (FAILED + FAILED_PERMANENT).

        The sibling of `count_active_for_pod`, and the half it does not cover.
        Both describe a file that will not answer a search, but only one of them
        resolves itself by waiting -- which is why they are counted separately
        rather than folded into one "not searchable" number that would be told
        to retry.
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(DatastoreFile)
                .where(
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.kind == "FILE",
                    DatastoreFile.search_enabled == True,  # noqa: E712
                    DatastoreFile.status.in_(
                        (
                            FileStatus.FAILED.value,
                            FileStatus.FAILED_PERMANENT.value,
                        )
                    ),
                )
            )
            or 0
        )
