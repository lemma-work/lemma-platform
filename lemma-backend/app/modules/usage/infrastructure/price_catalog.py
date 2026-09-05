"""Freeze a bundled provider rate card before any requests spend against it."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from genai_prices import Usage, __version__
from genai_prices.data import providers
from genai_prices.data_snapshot import DataSnapshot
from genai_prices.types import ModelPrice, Tier, TieredPrices
from pydantic import BaseModel, ConfigDict, Field

from app.modules.usage.contracts import ModelPricing
from app.modules.usage.domain.accounting import (
    CostSource,
    PricingUnavailableError,
    TokenCounts,
    money,
)


class Rate(BaseModel):
    model_config = ConfigDict(frozen=True)
    base: Decimal = Field(ge=0)
    tiers: tuple[tuple[int, Decimal], ...] = ()

    def maximum(self) -> Decimal:
        return (
            max(self.base, *(value for _, value in self.tiers))
            if self.tiers
            else self.base
        )


class RateCard(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: CostSource = CostSource.UNKNOWN
    provider: str | None = None
    model: str
    version: str = __version__
    enforceable: bool = False
    input_ceiling: int | None = None
    rates: dict[str, Rate] = Field(default_factory=dict)

    def price(self, counts: TokenCounts) -> Decimal | None:
        if self.source == CostSource.UNKNOWN:
            return None
        rates = {
            key: TieredPrices(
                value.base, [Tier(start, price) for start, price in value.tiers]
            )
            if value.tiers
            else value.base
            for key, value in self.rates.items()
        }
        return money(
            ModelPrice(**rates).calc_price(
                Usage(
                    input_tokens=counts.input_tokens,
                    output_tokens=counts.output_tokens,
                    cache_read_tokens=counts.cache_read_tokens,
                    cache_write_tokens=counts.cache_write_tokens,
                    input_audio_tokens=counts.input_audio_tokens,
                    output_audio_tokens=counts.output_audio_tokens,
                    cache_audio_read_tokens=counts.cache_audio_read_tokens,
                )
            )["total_price"]
        )

    def bound(self, output_ceiling: int) -> Decimal:
        if not self.enforceable or self.input_ceiling is None:
            raise PricingUnavailableError(
                "This model needs a provider price and input ceiling before monetary limits can be enforced."
            )
        input_rate = max(
            (
                rate.maximum()
                for name, rate in self.rates.items()
                if name.endswith("_mtok") and "output" not in name
            ),
            default=Decimal(0),
        )
        output_rate = max(
            (
                rate.maximum()
                for name, rate in self.rates.items()
                if name.endswith("_mtok") and "output" in name
            ),
            default=Decimal(0),
        )
        return money(
            (input_rate * self.input_ceiling + output_rate * output_ceiling) / 1_000_000
        )


def resolve_rate_card(
    profile: Mapping[str, object], overrides: Mapping[str, ModelPricing], now: datetime
) -> RateCard:
    model = str(
        profile.get("provider_model_name") or profile.get("model_name") or "unknown"
    )
    metadata = profile.get("model_metadata")
    ceiling = metadata.get("context_window") if isinstance(metadata, Mapping) else None
    ceiling = ceiling if isinstance(ceiling, int) and ceiling > 0 else None
    override = overrides.get(str(profile.get("model_name") or model)) or overrides.get(
        model
    )
    if override is not None:
        automatic = resolve_rate_card(profile, {}, now)
        return RateCard(
            source=CostSource.REGISTERED,
            model=model,
            enforceable=True,
            input_ceiling=ceiling or automatic.input_ceiling,
            version="registered",
            rates={
                "input_mtok": Rate(base=money(override.input_per_million_usd)),
                "output_mtok": Rate(base=money(override.output_per_million_usd)),
                "cache_read_mtok": Rate(
                    base=money(
                        override.cached_input_per_million_usd
                        if override.cached_input_per_million_usd is not None
                        else override.input_per_million_usd
                    )
                ),
                "cache_write_mtok": Rate(
                    base=money(
                        override.cache_write_per_million_usd
                        if override.cache_write_per_million_usd is not None
                        else override.input_per_million_usd
                    )
                ),
            },
        )
    return _automatic_rate_card(profile, model, ceiling, now)


def _automatic_rate_card(
    profile: Mapping[str, object], model: str, ceiling: int | None, now: datetime
) -> RateCard:
    config = profile.get("config")
    base_url = config.get("base_url") if isinstance(config, Mapping) else None
    if not base_url and profile.get("protocol") == "ANTHROPIC_COMPATIBLE":
        base_url = "https://api.anthropic.com"
    snapshot = DataSnapshot(providers=providers, from_auto_update=False)
    for known_provider in (True, False):
        if known_provider and not isinstance(base_url, str):
            continue
        try:
            provider, info = snapshot.find_provider_model(
                model, None, None, base_url if known_provider else None
            )
        except LookupError:
            continue
        price = info.get_prices(now)
        rates: dict[str, Rate] = {}
        # Vendor values have no shape until checked at this adapter boundary.
        for key, value in vars(price).items():
            if isinstance(value, Decimal):
                rates[key] = Rate(base=value)
            elif isinstance(value, TieredPrices):
                rates[key] = Rate(
                    base=value.base,
                    tiers=tuple((tier.start, tier.price) for tier in value.tiers),
                )
        return RateCard(
            source=CostSource.ESTIMATED,
            provider=provider.id,
            model=info.id,
            input_ceiling=ceiling or info.context_window,
            rates=rates,
            enforceable=known_provider,
        )
    return RateCard(model=model)
