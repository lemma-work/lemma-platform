"""Pod access and persistence adapters for bundle jobs."""

from app.modules.pod.api.dependencies import (
    PodEditorDep,
    PodViewerDep,
    get_pod_member_service,
    get_pod_service,
)


class PodRepository:
    def __new__(cls, *args, **kwargs):
        from app.modules.pod.infrastructure.pod_repositories import (
            PodRepository as implementation,
        )

        return implementation(*args, **kwargs)

__all__ = [
    "PodEditorDep",
    "PodRepository",
    "PodViewerDep",
    "get_pod_member_service",
    "get_pod_service",
]
