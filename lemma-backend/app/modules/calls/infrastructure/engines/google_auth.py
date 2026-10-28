"""How a Gemini Live connection proves who it is: an API key, or the
workload's own Google Cloud service account through Vertex AI.

The service account is read from the metadata server that GKE (with Workload
Identity) and Compute Engine give every workload, so no key is stored and
nothing is added to the image. A token is asked for on every connect, not
kept: a call reconnects every few minutes, and the metadata server answers
in a millisecond and refreshes its own tokens ahead of expiry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

import httpx

from app.core.log.log import get_logger
from app.modules.calls.domain.voice import EngineUnavailable

logger = get_logger(__name__)

type Headers = Callable[[], Awaitable[Mapping[str, str]]]

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_METADATA_TIMEOUT_SECONDS = 5.0
_VERTEX_LIVE_PATH = "/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"


def api_key_headers(api_key: str) -> Headers:
    async def headers() -> Mapping[str, str]:
        return {"x-goog-api-key": api_key}

    return headers


def service_account_headers(
    token_url: str = _METADATA_TOKEN,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Headers:
    async def headers() -> Mapping[str, str]:
        try:
            # Never through a proxy: the metadata server answers only directly.
            async with httpx.AsyncClient(
                timeout=_METADATA_TIMEOUT_SECONDS, transport=transport, trust_env=False
            ) as client:
                response = await client.get(
                    token_url, headers={"Metadata-Flavor": "Google"}
                )
                response.raise_for_status()
                token = response.json()["access_token"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("calls.gemini.token.failed", exc_info=True)
            raise EngineUnavailable(
                "The voice could not get this server's Google Cloud credentials."
            ) from exc
        return {"Authorization": f"Bearer {token}"}

    return headers


def vertex_endpoint(location: str) -> str:
    host = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    return f"wss://{host}{_VERTEX_LIVE_PATH}"


def vertex_model(project: str, location: str, model: str) -> str:
    if model.startswith("projects/"):
        return model
    name = model.removeprefix("models/")
    return f"projects/{project}/locations/{location}/publishers/google/models/{name}"
