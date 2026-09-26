"""Testing a saved model provider from the Models page.

Two questions, asked the way a run would ask them: does the provider list its
models for this key, and does the default model answer one short message. The
listing alone is not enough -- plenty of servers list models to anyone and then
refuse the completion -- and the completion alone cannot tell "wrong key" from
"no such model" as clearly as the listing's 401 can.

The answer is always one of a few fixed sentences. Provider error bodies are
never passed through: they are unbounded, sometimes echo the request (headers
included), and are written for the provider's own developers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from pydantic_ai.direct import model_request
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
)
from app.modules.agent.infrastructure.transport_errors import (
    local_model_server_name,
)
from app.modules.agent.services import runtime_model_factory
from app.modules.agent.services import runtime_provider_discovery as discovery
from app.modules.agent.services.runtime_profile_service import ResolvedAgentRuntime

logger = get_logger(__name__)

# Long enough for a cold local model to load its weights, short enough that a
# black-holed address does not hold the request until the proxy gives up.
_COMPLETION_TIMEOUT_SECONDS = 60.0
# The reply is thrown away; this only has to be enough for any model to emit
# something rather than fail on a zero budget.
_COMPLETION_MAX_TOKENS = 16
_KEY_REJECTED_STATUSES = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class ProviderCheckResult:
    ok: bool
    message: str
    #: What the provider listed, when it did. ``None`` rather than empty when
    #: the listing was not readable, so the page can tell the two apart.
    models: list[str] | None = None


type _ModelLister = Callable[..., Awaitable[list[discovery.DiscoveredModel]]]
type _ModelBuilder = Callable[..., Model | None]


@dataclass(frozen=True, slots=True)
class ProviderCheckCollaborators:
    """The three things a check talks to, so a test can stand in for the
    provider without patching this module's own functions.

    ``None`` means the real one, looked up at call time.
    """

    list_openai_compatible: _ModelLister | None = None
    list_anthropic_compatible: _ModelLister | None = None
    build_model: _ModelBuilder | None = None

    def openai_lister(self) -> _ModelLister:
        return self.list_openai_compatible or discovery.list_openai_compatible_models

    def anthropic_lister(self) -> _ModelLister:
        return (
            self.list_anthropic_compatible or discovery.list_anthropic_compatible_models
        )

    def model_builder(self) -> _ModelBuilder:
        return self.build_model or runtime_model_factory.connection_check_model


class _CheckFailed(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def check_saved_provider_connection(
    resolved: ResolvedAgentRuntime,
    *,
    collaborators: ProviderCheckCollaborators | None = None,
) -> ProviderCheckResult:
    """List the provider's models, then send its default model one message.

    Makes network calls only, so the caller runs it with the database
    connection released. Raises ``ValueError`` for a profile that is not a
    model provider -- a coding agent is tested by its computer being online.
    """
    profile = resolved.profile
    if profile.kind is not RuntimeProfileKind.MODEL_PROVIDER:
        raise ValueError("Only a model provider can be tested")
    seams = collaborators or ProviderCheckCollaborators()
    try:
        models = await _listed_models(resolved, seams)
        await _answer_one_message(resolved, seams)
    except _CheckFailed as failed:
        return ProviderCheckResult(ok=False, message=failed.message)
    model_name = resolved.model.name if resolved.model else profile.default_model_name
    return ProviderCheckResult(
        ok=True,
        message=f"{profile.name} answered using {model_name}.",
        models=models,
    )


async def _listed_models(
    resolved: ResolvedAgentRuntime, seams: ProviderCheckCollaborators
) -> list[str] | None:
    """The provider's model names, or ``None`` when it lists none it will share.

    A listing that is merely unreadable does not fail the test: some
    OpenAI-compatible servers have no ``/models`` route at all, and whether
    they work is exactly what the completion below finds out.
    """
    profile = resolved.profile
    api_key = _api_key(resolved)
    try:
        if profile.protocol is RuntimeProfileProtocol.OPENAI_COMPATIBLE:
            openai_config = OpenAICompatibleRuntimeConfig.model_validate(
                _config_data(profile.config)
            )
            listed = await seams.openai_lister()(
                base_url=str(openai_config.base_url),
                api_key=api_key,
                headers=openai_config.headers,
            )
        elif profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
            anthropic_config = AnthropicCompatibleRuntimeConfig.model_validate(
                _config_data(profile.config)
            )
            listed = await seams.anthropic_lister()(
                base_url=str(anthropic_config.base_url or "https://api.anthropic.com"),
                api_key=api_key or "",
                headers=anthropic_config.headers,
            )
        else:
            # Azure and Vertex have no listing Lemma reads; the completion is
            # the whole test for them.
            return None
    except discovery.ProviderKeyRejectedError as exc:
        raise _CheckFailed(discovery.key_rejected_message(profile.name)) from exc
    except discovery.ProviderUnreachableError as exc:
        raise _CheckFailed(
            _unreachable_message(profile.name, exc.local_server_name)
        ) from exc
    except discovery.ProviderListingError:
        return None
    return [model.name for model in listed] or None


async def _answer_one_message(
    resolved: ResolvedAgentRuntime, seams: ProviderCheckCollaborators
) -> None:
    profile = resolved.profile
    model = seams.model_builder()(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials,
    )
    if model is None:
        raise _CheckFailed(f"{profile.name} can't be tested from here.")
    try:
        async with asyncio.timeout(_COMPLETION_TIMEOUT_SECONDS):
            await model_request(
                model,
                [ModelRequest.user_text_prompt("Reply with the word OK.")],
                model_settings=ModelSettings(max_tokens=_COMPLETION_MAX_TOKENS),
            )
    except ModelHTTPError as exc:
        _log_failure(resolved, exc)
        if exc.status_code in _KEY_REJECTED_STATUSES:
            raise _CheckFailed(discovery.key_rejected_message(profile.name)) from exc
        raise _CheckFailed(_did_not_answer_message(resolved)) from exc
    except (TimeoutError, httpx.TimeoutException) as exc:
        _log_failure(resolved, exc)
        raise _CheckFailed(_unreachable_message(profile.name, None)) from exc
    except (ModelAPIError, UnexpectedModelBehavior, httpx.HTTPError) as exc:
        # Anything else the provider or its SDK raises -- a refused connection
        # wrapped in the SDK's own error type, a response it could not parse.
        # Logged in full here; the person gets a fixed sentence, never the
        # provider's text.
        _log_failure(resolved, exc)
        server = local_model_server_name(exc)
        if server is not None:
            raise _CheckFailed(_unreachable_message(profile.name, server)) from exc
        raise _CheckFailed(_did_not_answer_message(resolved)) from exc


def _config_data(config: object) -> object:
    """The stored config as plain data, whichever shape it was read back in.

    A row read from the database can come back as a raw dict rather than the
    protocol's config class, so both are accepted and re-validated here.
    """
    model_dump = getattr(config, "model_dump", None)
    return model_dump() if callable(model_dump) else config


def _api_key(resolved: ResolvedAgentRuntime) -> str | None:
    value = (resolved.credentials or {}).get("api_key")
    return value if isinstance(value, str) and value else None


def _unreachable_message(provider_name: str, local_server_name: str | None) -> str:
    if local_server_name is not None:
        return f"{local_server_name} isn't running on this computer."
    return f"Couldn't reach {provider_name}. Check its address."


def _did_not_answer_message(resolved: ResolvedAgentRuntime) -> str:
    model_name = (
        resolved.model.name if resolved.model else resolved.profile.default_model_name
    )
    return f"{resolved.profile.name} didn't answer a test message on {model_name}."


def _log_failure(resolved: ResolvedAgentRuntime, exc: BaseException) -> None:
    # Info, not warning: a provider refusing a test is the test working. The
    # traceback is kept so an operator can see what the provider actually said.
    logger.info(
        "agent.runtime_profile.connection_check_failed.observed",
        profile_id=resolved.profile.id,
        protocol=resolved.profile.protocol.value,
        exc_info=exc,
    )


__all__ = ["ProviderCheckResult", "check_saved_provider_connection"]
