"""Building the engine a call talks through, from configuration."""

from __future__ import annotations

from pydantic import SecretStr

from app.core.config import reveal_secret
from app.modules.calls.config import CallsSettings, VoiceEngineName
from app.modules.calls.domain.voice import EngineUnavailable, VoiceEngine
from app.modules.calls.domain.party import CallParty


def engines_offered(settings: CallsSettings) -> list[VoiceEngineName]:
    """The engines this deployment has keys for, the configured default first."""
    engines: list[VoiceEngineName] = []
    if settings.gemini_api_key is not None or settings.gemini_vertex_project:
        engines.append("gemini_live")
    if settings.openai_api_key is not None:
        engines.append("openai_realtime")
    if settings.deepgram_api_key is not None:
        engines.append("cascade")
    return sorted(engines, key=lambda engine: engine != settings.call_voice_engine)


async def build_engine(
    name: VoiceEngineName,
    *,
    settings: CallsSettings,
    instructions: str,
    party: CallParty,
) -> VoiceEngine:
    match name:
        case "gemini_live":
            from app.modules.calls.infrastructure.engines import google_auth
            from app.modules.calls.infrastructure.engines.gemini_live import (
                GeminiLiveEngine,
            )

            project = settings.gemini_vertex_project
            if project:
                # The service account wins over a key: no secret to hold.
                location = settings.gemini_vertex_location
                return GeminiLiveEngine(
                    headers=google_auth.service_account_headers(),
                    endpoint=google_auth.vertex_endpoint(location),
                    model=google_auth.vertex_model(
                        project, location, settings.gemini_live_model
                    ),
                    instructions=instructions,
                    voice=settings.gemini_live_voice,
                )
            return GeminiLiveEngine(
                headers=google_auth.api_key_headers(
                    _key(settings.gemini_api_key, "Gemini")
                ),
                model=settings.gemini_live_model,
                instructions=instructions,
                voice=settings.gemini_live_voice,
            )
        case "openai_realtime":
            from app.modules.calls.infrastructure.engines.openai_realtime import (
                OpenAIRealtimeEngine,
            )

            return OpenAIRealtimeEngine(
                api_key=_key(settings.openai_api_key, "OpenAI"),
                model=settings.openai_realtime_model,
                instructions=instructions,
                voice=settings.openai_realtime_voice,
            )
        case "cascade":
            return await _cascade(
                settings=settings, instructions=instructions, party=party
            )


async def _cascade(
    *, settings: CallsSettings, instructions: str, party: CallParty
) -> VoiceEngine:
    from app.modules.calls.infrastructure.engines.cascade import CascadeEngine
    from app.modules.calls.infrastructure.engines.deepgram import (
        AuraSpeaker,
        FluxListener,
    )

    from app.modules.calls.infrastructure.engines.talker import LemmaTalker

    key = _key(settings.deepgram_api_key, "Deepgram")
    talker = await LemmaTalker.resolve(
        model_name=settings.cascade_talker_model,
        instructions=instructions,
        party=party,
        reasoning_effort=settings.cascade_talker_reasoning_effort,
    )
    return CascadeEngine(
        listener=FluxListener(
            api_key=key,
            model=settings.cascade_stt_model,
            eager_eot_threshold=settings.cascade_eager_eot_threshold,
        ),
        speaker=AuraSpeaker(api_key=key, voice=settings.cascade_tts_voice),
        talker=talker,
    )


def _key(secret: SecretStr | None, provider: str) -> str:
    value = reveal_secret(secret)
    if not value:
        raise EngineUnavailable(
            f"Calls through {provider} are not set up on this server."
        )
    return value
