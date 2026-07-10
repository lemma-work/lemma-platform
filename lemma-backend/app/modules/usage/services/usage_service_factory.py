"""Single construction point for :class:`UsageService`.

All call sites (usage API, agent runner, sub-agent, pydantic-ai tracking) build
the service through here so the billing-backed limit port (when present) is wired
in one place and nobody outside billing imports billing.
"""

from __future__ import annotations

from app.modules.usage.infrastructure.repositories import UsageRepository
from app.modules.usage.services.usage_limit_provider import build_usage_limit_port
from app.modules.usage.services.usage_service import UsageService


def build_usage_service(uow: object) -> UsageService:
    return UsageService(
        usage_repository=UsageRepository(uow),
        usage_limit_port=build_usage_limit_port(uow),
    )
