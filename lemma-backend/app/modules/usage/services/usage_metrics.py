"""``lemma.llm.tokens`` and ``lemma.llm.cost_usd``: model usage as metrics."""

from __future__ import annotations

from uuid import UUID

from opentelemetry import metrics

meter = metrics.get_meter("app.modules.usage.services.usage_service")
token_counter = meter.create_counter("lemma.llm.tokens")
cost_counter = meter.create_counter("lemma.llm.cost_usd")


def record_usage_metrics(
    *,
    organization_id: UUID | None,
    model_name: str,
    usage_kind: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
) -> None:
    labels = {
        "gen_ai.request.model": model_name,
        "operation": usage_kind,
        # Spend by customer: one series per organization, bounded by the org
        # count and acceptable at current scale. To remove it, drop
        # ``billing.organization_id`` from METRIC_ATTRIBUTE_KEYS
        # (span_sanitizer.py) and the export view strips it everywhere.
        "billing.organization_id": str(organization_id) if organization_id else "none",
    }
    if input_tokens > 0:
        token_counter.add(input_tokens, {**labels, "gen_ai.token.type": "input"})
    if output_tokens > 0:
        token_counter.add(output_tokens, {**labels, "gen_ai.token.type": "output"})
    if cost_usd:
        cost_counter.add(cost_usd, labels)
