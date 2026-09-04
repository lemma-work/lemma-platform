"""Whether this deployment can price every model it offers.

A startup assertion rather than a service method: it answers a question about
the *catalog*, asked once when a deployment boots, and nothing in the request
path calls it. Kept out of `usage_service` for the reason `limit_windows` and
`reservation_sizing` are -- that file's length is ratcheted, and none of this
touches storage or the limit port.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.modules.usage.contracts import ModelPricing
from app.modules.usage.domain.entities import CostSource
from app.modules.usage.services.cost_resolver import UsageTokens, resolve_cost


def assert_system_pricing_covers_catalog(
    model_names: Iterable[tuple[str, str | None]],
    *,
    pricing: Mapping[str, ModelPricing] | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Return the system models nothing can price (empty == all priceable).

    "Covered" now means *either* layer answers: a registered entry under the
    public name or the provider id, or the public dataset recognising the model.
    A model reaches this list only when both miss, and that is the case worth an
    operator's attention -- it meters with a null cost, so it counts toward no
    spend limit and is effectively free to run.

    Missing prices still never prevent metering or refuse a run (`PS-OPS-011`);
    this only reports completeness.
    """
    # Deferred: the service imports this module at load, so naming it up here
    # would close the loop. Only the default needs it.
    from app.modules.usage.services.usage_service import UsageService

    table = pricing if pricing is not None else UsageService._SYSTEM_MODEL_PRICING
    probe = UsageTokens(input_tokens=1)
    uncovered: list[str] = []
    for public_name, provider_name in model_names:
        resolved = resolve_cost(
            model_name=public_name,
            provider_model_name=provider_name,
            base_url=base_url,
            tokens=probe,
            pricing_table=dict(table),
        )
        if resolved.source is CostSource.UNKNOWN:
            uncovered.append(public_name or provider_name or "<unknown>")
    return uncovered
