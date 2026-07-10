"""Pricing, quoting, and usage-value normalization for system model runs."""

from __future__ import annotations

import json
import os

from app.core.log.log import get_logger
from app.modules.usage.contracts import ModelPricing

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
                    cached_input_per_million_usd=(
                        float(values["cached_input_per_million_usd"])
                        if values.get("cached_input_per_million_usd") is not None
                        else None
                    ),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "Invalid system model usage metadata configuration",
                error_type=type(exc).__name__,
            )
            cls._ENV_METADATA_SOURCE = raw
            return
        cls._SYSTEM_MODEL_PRICING.update(pricing)
        cls._ENV_METADATA_SOURCE = raw

    def _calculate_system_cost(
        self,
        *,
        profile_scope: str,
        model_name: str,
        provider_model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        units: float,
        cache_read_tokens: int = 0,
    ) -> tuple[float | None, bool]:
        if not self._is_system_scope(profile_scope):
            return None, False
        pricing, pricing_missing = self._resolve_pricing(model_name, provider_model_name)
        if pricing is None:
            return None, True
        total_input = max(0, input_tokens)
        cache_read = min(max(0, cache_read_tokens), total_input)
        non_cached = total_input - cache_read
        cached_rate = (
            pricing.cached_input_per_million_usd
            if pricing.cached_input_per_million_usd is not None
            else pricing.input_per_million_usd
        )
        input_cost = (
            non_cached / 1_000_000 * pricing.input_per_million_usd
            + cache_read / 1_000_000 * cached_rate
        )
        output_cost = (
            max(0, output_tokens) / 1_000_000
        ) * pricing.output_per_million_usd
        unit_cost = max(0.0, units) * pricing.unit_usd
        return round(input_cost + output_cost + unit_cost, 8), pricing_missing

    def _resolve_pricing(
        self, model_name: str, provider_model_name: str | None
    ) -> tuple[ModelPricing | None, bool]:
        for candidate in (model_name, provider_model_name):
            if candidate and candidate.strip() in self._SYSTEM_MODEL_PRICING:
                return self._SYSTEM_MODEL_PRICING[candidate.strip()], False
        logger.debug(
            "Usage pricing is not registered; recording without cost",
            model_name=model_name,
            provider_model_name=provider_model_name,
        )
        return None, True

    @staticmethod
    def _coerce_token_count(value: object) -> int:
        try:
            return max(0, int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
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
            except (TypeError, ValueError):
                continue
        return 0
