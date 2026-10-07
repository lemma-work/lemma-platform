"""The roles on the hiring shelf: every shipped template that carries a card."""

from __future__ import annotations

from app.core.concurrency.offload import run_blocking
from app.modules.pod_bundle.domain.template_card import TemplateCard
from app.modules.pod_bundle.infrastructure.template_cards import template_cards


async def list_template_cards() -> list[TemplateCard]:
    return await run_blocking(template_cards)
