"""Decisions answered by Google's Decisions API (`gemini-decision-1`).

Signed in with a Gemini API key. Google serves this API on the Gemini API only,
and refuses service accounts there ("Access to Gemini API is restricted with
service accounts"), so a deployment that reaches Gemini models through Vertex AI
as a service account still needs a key for decisions until Google brings them
to Vertex. Without one, the registry falls back to the deployment's own model.
"""

from __future__ import annotations

import httpx

from app.modules.decisions.infrastructure.providers.decision_wire import GEMINI
from app.modules.decisions.infrastructure.providers.wire_provider import (
    MeterOpener,
    WireDecisionProvider,
)


class GeminiDecisionProvider(WireDecisionProvider):
    provider_name = "gemini"
    dialect = GEMINI

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
            url=f"{base_url.rstrip('/')}/decisions",
            model=model,
            price_per_million_input_tokens_usd=price_per_million_input_tokens_usd,
            client=client,
            meter=meter,
        )
        self._api_key = api_key

    async def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}
