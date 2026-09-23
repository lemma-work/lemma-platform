"""The voices a provider actually has, read from the provider.

`deepgram_provider` used to carry one hand-picked voice per language in a dict.
Aura-2 ships roughly eighty across seven languages, each with an accent, an age
and use-case tags, so the dict was both a tiny slice of what exists and a thing
that goes stale on its own — which the provider's own 400-on-a-retired-voice
fallback was there to survive.

Reading the catalogue removes both problems, and it is the only honest source:
while writing this, two of Deepgram's own documentation pages disagreed about
which languages Aura-2 covers. The API does not have opinions.

Cached in Redis, per this codebase's rule that data caching goes through
`RedisJsonCache` and never an in-process dict: an API key is per-deployment, and
a per-process copy would mean each worker holding its own slightly different
idea of what voices exist.
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass

import httpx
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings

logger = get_logger(__name__)

MODELS_URL = "https://api.deepgram.com/v1/models"
_CACHE_PREFIX = "agent:speech-voices"
_FETCH_TIMEOUT_SECONDS = 15.0

#: Long, because the catalogue is a provider's product line rather than this
#: deployment's data: it changes when Deepgram ships voices, not when anybody
#: here does anything.
_CACHE_TTL_SECONDS = 24 * 60 * 60

#: Used when the catalogue cannot be read — no key, no network, a provider
#: outage. One known-good voice per language it has ever shipped, so `say` in
#: Spanish still sounds Spanish while the listing is unavailable. Not the
#: source of truth, and deliberately not extended: anything richer belongs in
#: the catalogue this falls back from.
FALLBACK_VOICE_BY_LANGUAGE: dict[str, str] = {
    "en": "aura-2-thalia-en",
    "es": "aura-2-celeste-es",
    "de": "aura-2-elara-de",
    "fr": "aura-2-agathe-fr",
    "nl": "aura-2-beatrix-nl",
    "it": "aura-2-melia-it",
    "ja": "aura-2-uzume-ja",
}

_voice_cache: RedisJsonCache | None = None


@dataclass(frozen=True, slots=True)
class Voice:
    """One speakable voice, as the provider describes it."""

    name: str
    languages: tuple[str, ...]
    accent: str = ""
    tags: tuple[str, ...] = ()

    def speaks(self, language: str) -> bool:
        base = _base_language(language)
        return any(_base_language(code) == base for code in self.languages)


def _base_language(code: str) -> str:
    return str(code or "").strip().lower().replace("_", "-").split("-")[0]


def _get_cache() -> RedisJsonCache:
    global _voice_cache
    if _voice_cache is None:
        _voice_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_CACHE_PREFIX,
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
    return _voice_cache


def _parse(payload: object) -> tuple[Voice, ...]:
    """The TTS half of a `/v1/models` response, as voices.

    `canonical_name` is the string the speak endpoint takes (`aura-2-thalia-en`);
    `name` is the bare voice ("thalia"), which is not what a caller can pass.
    Entries missing the canonical name are dropped rather than guessed at.
    """
    entries = payload.get("tts") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return ()
    voices: list[Voice] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical_name") or "").strip()
        if not canonical:
            continue
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        languages = entry.get("languages")
        tags = metadata.get("tags")
        voices.append(
            Voice(
                name=canonical,
                languages=tuple(str(code) for code in languages)
                if isinstance(languages, list)
                else (),
                accent=str(metadata.get("accent") or ""),
                tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
            )
        )
    return tuple(voices)


async def load_voices(
    *, api_key: str | None = None, cache: RedisJsonCache | None = None
) -> tuple[Voice, ...]:
    """Every TTS voice this deployment's key can use, cached. Empty when unknown.

    Empty is a real answer and not an error: without a key, or with the provider
    unreachable, the caller falls back to a known voice rather than refusing to
    speak. A failure to *list* voices must never be a failure to say something.

    `cache` is a parameter so a test can hand in one that fails, which is the
    only way to cover the degraded paths below without reaching into this
    module to replace a name inside it.
    """
    key = api_key or agent_settings.deepgram_api_key
    if not key:
        return ()

    cache = cache or _get_cache()
    # A cache that cannot be reached is a miss, not a failure. This function
    # promises above that listing voices never stops the caller speaking, and
    # Redis being down is exactly the moment that promise has to hold: the
    # fallback map is right there, and propagating would take `say` down with
    # the cache.
    try:
        cached = await cache.get_json("deepgram")
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        logger.warning(
            "agent.speech.voice_catalogue_cache_unreadable.degraded",
            error_type=type(exc).__name__,
        )
        cached = None
    if isinstance(cached, list):
        return _parse({"tts": cached})

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(
                MODELS_URL, headers={"Authorization": f"Token {key}"}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "agent.speech.voice_catalogue_unavailable.degraded",
            error_type=type(exc).__name__,
        )
        return ()

    entries = payload.get("tts") if isinstance(payload, dict) else None
    if isinstance(entries, list):
        # Non-fatal for the same reason: the catalogue is in hand, and failing
        # to store it for next time is no reason to withhold it from this call.
        try:
            await cache.set_json("deepgram", entries)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "agent.speech.voice_catalogue_cache_unwritable.degraded",
                error_type=type(exc).__name__,
            )
    return _parse(payload)


def default_voice_for(language: str | None, voices: tuple[Voice, ...]) -> str | None:
    """A voice that speaks ``language``, preferring the catalogue over the map.

    None means the provider has none for that language — Hindi is the live
    example — and the caller must say so rather than reading Hindi aloud in an
    English accent.
    """
    base = _base_language(language or "")
    if not base:
        return None
    for voice in voices:
        if voice.speaks(base):
            return voice.name
    return FALLBACK_VOICE_BY_LANGUAGE.get(base)
