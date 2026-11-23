"""Decisions answered by Typesafe System One, signed in with its API key."""

from __future__ import annotations

import httpx

from app.modules.decisions.infrastructure.providers.decision_wire import TYPESAFE
from app.modules.decisions.infrastructure.providers.wire_provider import (
    MeterOpener,
    WireDecisionProvider,
)


class TypesafeDecisionProvider(WireDecisionProvider):
    provider_name = "typesafe"
    dialect = TYPESAFE

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        price_per_million_input_tokens_usd: float | None = None,
        client: httpx.AsyncClient | None = None,
        meter: MeterOpener | None = None,
    ) -> None:
        super().__init__(
            url=f"{base_url.rstrip('/')}/systemone",
            model=model,
            price_per_million_input_tokens_usd=price_per_million_input_tokens_usd,
            client=client,
            meter=meter,
        )
        self._api_key = api_key

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}
