"""What a model call cost, resolved in layers that degrade honestly.

Pricing used to be one hand-written table. That works for `system:lemma` -- the
models this deployment chose and an operator maintains rates for -- and for
nothing else. A runtime profile someone adds with their own key runs a model
nobody here has ever priced, so every one of those rows recorded a null cost and
the person who added the profile could not see what their agents were spending.

Three layers, most specific first:

1. **A registered override** (`UsageService.register_model_pricing`). Explicit
   rates win, so a negotiated price -- or a correction to the public dataset --
   is always what gets charged.
2. **`genai-prices`**, the dataset behind pydantic-ai's own usage extraction, and
   already a dependency because of it. It knows 33 providers, resolves one from
   the profile's base URL alone, and handles what a four-field table cannot:
   cache-write rates, tiered pricing above a context threshold, audio tokens.
3. **Nothing.** The cost is *unknown*, not zero. `PS-OPS-001` requires the
   distinction and `PS-OPS-011` requires that a missing price never refuses work.

Which layer answered is recorded on the row as ``cost_source``.

One resolution detail is load-bearing: `genai-prices` is keyed by the *provider*
model id, so `accounts/fireworks/models/glm-5p2` resolves and the public name
`glm-5.2` does not. Every lookup here tries the provider id first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from genai_prices import Usage as PriceUsage, calc_price

from app.core.log.log import get_logger
from app.modules.usage.contracts import ModelPricing
from app.modules.usage.domain.entities import CostSource

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UsageTokens:
    """The token counts a single model call reported.

    ``input_tokens`` is the *inclusive* total: both cache buckets are subsets of
    it, never additions to it. That is the convention every provider mapping in
    `genai-prices` normalizes to, and getting it backwards would price cached
    tokens twice.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    units: float = 0.0

    def normalized(self) -> "UsageTokens":
        """The same counts with the cache buckets clamped inside the input total.

        A provider that reports more cached tokens than input tokens is either
        wrong or using a convention we do not know; either way the arithmetic
        below must not produce a negative uncached count.
        """
        total_input = max(0, self.input_tokens)
        cache_read = min(max(0, self.cache_read_tokens), total_input)
        cache_write = min(max(0, self.cache_write_tokens), total_input - cache_read)
        return UsageTokens(
            input_tokens=total_input,
            output_tokens=max(0, self.output_tokens),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            units=max(0.0, self.units),
        )

    @property
    def uncached_input_tokens(self) -> int:
        return max(
            0,
            self.input_tokens - self.cache_read_tokens - self.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class ResolvedCost:
    """A cost and the provenance of the number."""

    cost_usd: float | None
    source: CostSource

    @classmethod
    def unknown(cls) -> "ResolvedCost":
        return cls(cost_usd=None, source=CostSource.UNKNOWN)


@dataclass(frozen=True, slots=True)
class RecordCost:
    """Everything a usage row needs to say about what it cost."""

    cost_usd: float | None
    cost_source: CostSource
    cache_read_tokens: int
    cache_write_tokens: int
    metadata: dict[str, object]


def resolve_cost(
    *,
    model_name: str,
    provider_model_name: str | None,
    base_url: str | None,
    tokens: UsageTokens,
    occurred_at: datetime | None = None,
    pricing_table: dict[str, ModelPricing],
) -> ResolvedCost:
    """Price one model call through the layers, or report it unpriceable."""
    counts = tokens.normalized()
    registered = _registered_pricing(model_name, provider_model_name, pricing_table)
    if registered is not None:
        return ResolvedCost(
            cost_usd=_registered_cost(registered, counts),
            source=CostSource.REGISTERED,
        )
    estimated = _estimated_cost(
        model_name=model_name,
        provider_model_name=provider_model_name,
        base_url=base_url,
        tokens=counts,
        occurred_at=occurred_at,
    )
    if estimated is not None:
        return ResolvedCost(cost_usd=estimated, source=CostSource.ESTIMATED)
    logger.debug("usage.cost_resolver.model_could_not_be_priced.observed")
    return ResolvedCost.unknown()


def _registered_pricing(
    model_name: str,
    provider_model_name: str | None,
    pricing_table: dict[str, ModelPricing],
) -> ModelPricing | None:
    for candidate in (model_name, provider_model_name):
        if candidate and candidate.strip() in pricing_table:
            return pricing_table[candidate.strip()]
    return None


def _registered_cost(pricing: ModelPricing, tokens: UsageTokens) -> float:
    """Apply a registered rate card to already-normalized counts."""
    cached_rate = _rate_or_base(
        pricing.cached_input_per_million_usd, pricing.input_per_million_usd
    )
    cache_write_rate = _rate_or_base(
        pricing.cache_write_per_million_usd, pricing.input_per_million_usd
    )
    input_cost = (
        tokens.uncached_input_tokens / 1_000_000 * pricing.input_per_million_usd
        + tokens.cache_read_tokens / 1_000_000 * cached_rate
        + tokens.cache_write_tokens / 1_000_000 * cache_write_rate
    )
    output_cost = tokens.output_tokens / 1_000_000 * pricing.output_per_million_usd
    return round(input_cost + output_cost + tokens.units * pricing.unit_usd, 8)


def _rate_or_base(rate: float | None, base: float) -> float:
    return base if rate is None else rate


def _estimated_cost(
    *,
    model_name: str,
    provider_model_name: str | None,
    base_url: str | None,
    tokens: UsageTokens,
    occurred_at: datetime | None,
) -> float | None:
    """Best-effort price from the public dataset, or ``None`` if it cannot.

    Two attempts, and the order matters. The profile's base URL identifies the
    provider outright -- which is what lets any OpenAI-compatible profile someone
    added get a real cost with no per-model configuration -- so it is tried
    first. Falling back to the bare model reference catches a private gateway
    fronting a model the dataset knows anyway.
    """
    usage = PriceUsage(
        input_tokens=tokens.input_tokens,
        cache_read_tokens=tokens.cache_read_tokens,
        cache_write_tokens=tokens.cache_write_tokens,
        output_tokens=tokens.output_tokens,
    )
    for model_ref in _model_refs(model_name, provider_model_name):
        price = _price_for(
            usage=usage,
            model_ref=model_ref,
            base_url=base_url,
            occurred_at=occurred_at,
        )
        if price is not None:
            return price
    return None


def _model_refs(model_name: str, provider_model_name: str | None) -> list[str]:
    """Candidate references, provider id first -- see the module docstring."""
    refs: list[str] = []
    for candidate in (provider_model_name, model_name):
        stripped = (candidate or "").strip()
        if stripped and stripped not in refs:
            refs.append(stripped)
    return refs


def _price_for(
    *,
    usage: PriceUsage,
    model_ref: str,
    base_url: str | None,
    occurred_at: datetime | None,
) -> float | None:
    """One dataset lookup, by provider URL then by model reference alone.

    `LookupError` is the dataset's documented "I do not know this" and is caught
    narrowly on purpose: anything else coming out of a pricing lookup is a bug
    worth seeing, not a row that should quietly record a null cost.
    """
    if base_url:
        try:
            calculation = calc_price(
                usage,
                model_ref,
                provider_api_url=base_url,
                genai_request_timestamp=occurred_at,
            )
        except LookupError:
            pass
        else:
            return round(float(calculation.total_price), 8)
    try:
        calculation = calc_price(usage, model_ref, genai_request_timestamp=occurred_at)
    except LookupError:
        return None
    return round(float(calculation.total_price), 8)


def tokens_for_budget(
    *,
    model_name: str,
    provider_model_name: str | None,
    base_url: str | None,
    budget_usd: float,
    pricing_table: dict[str, ModelPricing],
) -> tuple[int, int] | None:
    """How many input and output tokens ``budget_usd`` buys on this model.

    ``None`` when the model cannot be priced at all. That is deliberate and it
    matches `PS-OPS-011`: a deployment whose pricing table has a gap must not
    start refusing work, so an unpriceable model runs unbounded exactly as it
    does today rather than being cut off at zero tokens.

    Two numbers rather than one because input and output are priced differently
    and a single combined cap would have to assume the worse rate for every
    token -- which would cut ordinary runs short to guard against a shape they
    do not have. Capping each side at the budget means a run that spends its
    whole allowance on input *and* its whole allowance on output can reach twice
    the budget; against an allowance that is otherwise unbounded, that is the
    right trade.
    """
    input_rate = _rate_per_token(
        UsageTokens(input_tokens=_PROBE_TOKENS),
        model_name=model_name,
        provider_model_name=provider_model_name,
        base_url=base_url,
        pricing_table=pricing_table,
    )
    output_rate = _rate_per_token(
        UsageTokens(output_tokens=_PROBE_TOKENS),
        model_name=model_name,
        provider_model_name=provider_model_name,
        base_url=base_url,
        pricing_table=pricing_table,
    )
    if input_rate is None or output_rate is None:
        return None
    budget = max(0.0, budget_usd)
    return (int(budget / input_rate), int(budget / output_rate))


#: How many tokens to price in order to learn what one costs. Small on purpose.
#: Anthropic's input rate doubles above a 200k-token context, so pricing a
#: million-token probe to derive a per-token rate answered with the *upper* tier
#: and halved every budget derived from it -- the cap came out twice as tight as
#: the allowance actually bought. Small enough to sit inside the first tier of
#: every model we serve, large enough that a rate quoted per million does not
#: round away.
_PROBE_TOKENS = 10_000


def _rate_per_token(
    probe: UsageTokens,
    *,
    model_name: str,
    provider_model_name: str | None,
    base_url: str | None,
    pricing_table: dict[str, ModelPricing],
) -> float | None:
    """What one token costs at the margin, or ``None`` if the model has no price.

    Marginal rather than average: a budget is spent forward from wherever the
    run already is, so the rate that matters is the one the next token pays.
    """
    priced = resolve_cost(
        model_name=model_name,
        provider_model_name=provider_model_name,
        base_url=base_url,
        tokens=probe,
        pricing_table=pricing_table,
    ).cost_usd
    if not priced:
        return None
    return priced / _PROBE_TOKENS
