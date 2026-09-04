"""Pricing, quoting, and usage-value normalization for model runs.

The arithmetic moved to :mod:`app.modules.usage.services.cost_resolver`; what is
left here is the part that belongs to :class:`UsageService` -- the registered
rate table, the environment override that seeds it, and the small readers that
pull numbers off whatever shape a runtime handed us.
"""

from __future__ import annotations

import json
import os

from app.core.log.log import get_logger
from app.modules.usage.contracts import AgentRunUsage, ModelPricing
from app.modules.usage.domain.entities import CostSource
from app.modules.usage.services.cost_resolver import (
    RecordCost,
    UsageTokens,
    resolve_cost,
)

logger = get_logger(__name__)


class UsagePricing:
    """Pricing responsibility mixed into :class:`UsageService`."""

    _SYSTEM_MODEL_PRICING: dict[str, ModelPricing]
    _ENV_METADATA_SOURCE: str | None

    @classmethod
    def _load_environment_metadata(cls) -> None:
        raw = os.getenv("LEMMA_SYSTEM_MODEL_METADATA_JSON")
        if not raw or raw == cls._ENV_METADATA_SOURCE:
            return
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("metadata must be a JSON object")
            pricing: dict[str, ModelPricing] = {}
            for model_name, values in payload.items():
                if not isinstance(model_name, str) or not isinstance(values, dict):
                    raise TypeError("model metadata entries must be objects")
                pricing[model_name] = ModelPricing(
                    input_per_million_usd=float(values["input_per_million_usd"]),
                    output_per_million_usd=float(values["output_per_million_usd"]),
                    unit_usd=float(values.get("unit_usd", 0.0)),
                    cached_input_per_million_usd=_optional_rate(
                        values.get("cached_input_per_million_usd")
                    ),
                    cache_write_per_million_usd=_optional_rate(
                        values.get("cache_write_per_million_usd")
                    ),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "usage.pricing.invalid_system_model_usage_metadata.failed",
                error_type=type(exc).__name__,
                exc_info=True,
            )
            cls._ENV_METADATA_SOURCE = raw
            return
        cls._SYSTEM_MODEL_PRICING.update(pricing)
        cls._ENV_METADATA_SOURCE = raw

    def _record_cost(
        self,
        *,
        runtime_profile: dict[str, object] | None,
        model_name: str,
        usage_data: AgentRunUsage,
    ) -> RecordCost:
        """Price one call and stamp the row's cost provenance.

        Scope used to gate this: only ``SYSTEM`` rows were priced, so a profile
        someone added with their own key recorded tokens and a null cost forever.
        Cost is now resolved for every scope, and the *limit* queries -- not this
        function -- are what keep a customer's own spend out of their Lemma
        allowance, by filtering on ``profile_scope == 'SYSTEM'``.
        """
        self._load_environment_metadata()
        metadata = dict(usage_data.metadata or {})
        cache_read = self._coerce_token_count(metadata.get("cache_read_tokens"))
        cache_write = self._coerce_token_count(metadata.get("cache_write_tokens"))
        provider_model_name = self._profile_value(
            runtime_profile, "provider_model_name"
        )
        resolved = resolve_cost(
            model_name=model_name,
            provider_model_name=provider_model_name,
            base_url=self._profile_base_url(runtime_profile),
            tokens=UsageTokens(
                input_tokens=usage_data.input_tokens,
                output_tokens=usage_data.output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                units=usage_data.units,
            ),
            pricing_table=self._SYSTEM_MODEL_PRICING,
        )
        if provider_model_name:
            metadata["provider_model_name"] = provider_model_name
        if resolved.source is CostSource.UNKNOWN:
            metadata["pricing_missing"] = True
        return RecordCost(
            cost_usd=resolved.cost_usd,
            cost_source=resolved.source,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            metadata=metadata,
        )

    @staticmethod
    def _coerce_token_count(value: object) -> int:
        try:
            return max(0, int(value))  # type: ignore[arg-type]
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _profile_value(
        runtime_profile: dict[str, object] | None, key: str
    ) -> str | None:
        if not isinstance(runtime_profile, dict):
            return None
        value = runtime_profile.get(key)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _profile_base_url(runtime_profile: dict[str, object] | None) -> str | None:
        """The provider URL a profile points at, when it carries one.

        This is what lets the public pricing dataset identify a provider for a
        profile nobody here configured rates for. A harness profile has no base
        URL and simply gets no hint, which the resolver handles.
        """
        if not isinstance(runtime_profile, dict):
            return None
        config = runtime_profile.get("config")
        if not isinstance(config, dict):
            return None
        base_url = config.get("base_url")
        return base_url if isinstance(base_url, str) and base_url else None

    @staticmethod
    def _is_system_scope(profile_scope: str) -> bool:
        return profile_scope == "SYSTEM"

    @staticmethod
    def _usage_value(usage: object, *names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            if value is None:
                continue
            try:
                return max(0, int(value))
            except TypeError, ValueError:
                continue
        return 0


def _optional_rate(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]
