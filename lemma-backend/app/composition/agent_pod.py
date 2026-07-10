"""Pod persistence adapters used by agent conversations."""

from app.modules.pod.infrastructure.pod_repositories import PodRepository


def create_agent_pod_repository(uow) -> PodRepository:
    return PodRepository(uow)
