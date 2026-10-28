"""Send a provider's model one message, and say how it went in Lemma's terms.

The Models page's "Test connection" (``application/agents/check_provider``)
asks this to send one tiny completion. The provider SDK's and pydantic-ai's
errors stop here: what comes back is either nothing (it answered) or a
``ProviderDidNotAnswer`` saying why, by kind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from enum import StrEnum
from typing import cast

import httpx2
from pydantic_ai.direct import model_request
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models import Model

from app.modules.agent.infrastructure.providers import model_factory
from app.modules.agent.infrastructure.providers.transport_errors import (
    local_model_server_name,
)

_KEY_REJECTED_STATUSES = frozenset({401, 403})


class NoAnswer(StrEnum):
    KEY_REJECTED = "key_rejected"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"


class ProviderDidNotAnswer(Exception):
    """The test message got no answer; ``kind`` says why, ``cause`` is the SDK's."""

    def __init__(
        self,
        kind: NoAnswer,
        *,
        cause: BaseException,
        local_server_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.cause = cause
        self.local_server_name = local_server_name


async def send_test_message(
    model: object,
    *,
    model_settings: Mapping[str, object] | None,
    timeout_seconds: float,
) -> None:
    """Ask ``model`` to reply with one word, with the settings a run would send."""
    try:
        async with asyncio.timeout(timeout_seconds):
            await model_request(
                cast(Model, model),
                [ModelRequest.user_text_prompt("Reply with the word OK.")],
                model_settings=model_factory.provider_model_settings(model_settings),
            )
    except ModelHTTPError as exc:
        raise ProviderDidNotAnswer(
            NoAnswer.KEY_REJECTED
            if exc.status_code in _KEY_REJECTED_STATUSES
            else NoAnswer.REFUSED,
            cause=exc,
        ) from exc
    except (TimeoutError, httpx2.TimeoutException) as exc:
        raise ProviderDidNotAnswer(NoAnswer.UNREACHABLE, cause=exc) from exc
    except (ModelAPIError, UnexpectedModelBehavior, httpx2.HTTPError) as exc:
        # Anything else the provider or its SDK raises -- a refused connection
        # wrapped in the SDK's own error type, a response it could not parse.
        # `httpx2`, not `httpx`: the completion goes through the provider SDK's
        # client, and neither stack's errors subclass the other's.
        server = local_model_server_name(exc)
        raise ProviderDidNotAnswer(
            NoAnswer.UNREACHABLE if server is not None else NoAnswer.REFUSED,
            cause=exc,
            local_server_name=server,
        ) from exc


__all__ = ["NoAnswer", "ProviderDidNotAnswer", "send_test_message"]
