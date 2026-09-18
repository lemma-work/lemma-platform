"""Voices come from the provider, and a provider that cannot answer is not fatal.

The catalogue replaced a hand-written dict of one voice per language. Aura-2
ships roughly eighty across seven, so the dict was a slice — and a slice that
goes stale on its own, which the provider's 400-on-a-retired-voice fallback
existed to survive. While this was written, two of Deepgram's own documentation
pages disagreed about which languages are covered; the API does not.
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from app.modules.agent.tools.speech.voice_catalogue import (
    FALLBACK_VOICE_BY_LANGUAGE,
    Voice,
    _parse,
    default_voice_for,
)

pytestmark = pytest.mark.unit


def _entry(**overrides):
    entry = {
        "name": "thalia",
        "canonical_name": "aura-2-thalia-en",
        "languages": ["en", "en-US"],
        "metadata": {"accent": "American", "tags": ["warm"]},
    }
    entry.update(overrides)
    return entry


def test_a_voice_is_named_by_what_the_speak_endpoint_takes():
    """`name` is the bare voice ("thalia"); only `canonical_name` is callable."""
    (voice,) = _parse({"tts": [_entry()]})

    assert voice.name == "aura-2-thalia-en"
    assert voice.accent == "American"
    assert voice.tags == ("warm",)


def test_an_entry_with_no_canonical_name_is_dropped_rather_than_guessed_at():
    """Handing back a name the speak endpoint rejects is worse than omitting it."""
    assert _parse({"tts": [_entry(canonical_name="")]}) == ()
    assert _parse({"tts": [_entry(canonical_name=None)]}) == ()


@pytest.mark.parametrize(
    "payload", [{}, {"tts": None}, {"tts": "nope"}, {"stt": []}, None, []]
)
def test_a_response_that_is_not_a_catalogue_reads_as_no_voices(payload):
    """Empty is a real answer: `say` still works, it just picks for itself."""
    assert _parse(payload) == ()


def test_a_regional_code_matches_its_language():
    voice = Voice(name="aura-2-someone-es", languages=("es-419",))

    assert voice.speaks("es")
    assert voice.speaks("es-MX")
    assert not voice.speaks("en")


def test_the_fallback_covers_every_language_it_ever_shipped():
    """It is what answers while the provider is unreachable, so it must not be
    narrower than the thing it stands in for."""
    assert set(FALLBACK_VOICE_BY_LANGUAGE) == {"en", "es", "de", "fr", "nl", "it", "ja"}
    for language, voice in FALLBACK_VOICE_BY_LANGUAGE.items():
        assert voice.endswith(f"-{language}"), voice


def test_a_language_with_no_voice_is_told_so_rather_than_given_one():
    """Hindi is the live case: nova-3 transcribes it and Aura-2 cannot speak it.

    Returning an English voice would read Hindi aloud in an English accent,
    which is the same defect as a wrong transcript.
    """
    assert default_voice_for("hi", ()) is None


class _UnreachableCache:
    """A Redis that is down, in the two ways this module touches it."""

    def __init__(self) -> None:
        self.writes = 0

    async def get_json(self, suffix: str):
        raise RedisError("connection refused")

    async def set_json(self, suffix: str, value, **_kwargs) -> None:
        self.writes += 1
        raise RedisError("connection refused")


async def test_a_cache_that_is_down_does_not_stop_the_agent_speaking(monkeypatch):
    """This module's promise is that failing to *list* voices never stops
    something being said -- the fallback map exists for exactly that.

    Both cache calls sat outside the handler that keeps that promise, which
    covered only the HTTP fetch. A Redis outage therefore escaped `list_voices`
    and, through `voice_for_language`, made `say` fail before synthesis had
    even been attempted: the one moment the fallback was written for was the
    one moment it was skipped.
    """
    import httpx

    from app.modules.agent.tools.speech.voice_catalogue import load_voices

    class _OfflineClient:
        """The provider is unreachable too, so only the cache is under test."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("no network in a unit test")

    monkeypatch.setattr(httpx, "AsyncClient", _OfflineClient)
    cache = _UnreachableCache()

    voices = await load_voices(api_key="test-key", cache=cache)

    assert voices == (), "an unreadable cache is a miss, not an exception"


def test_the_fallback_still_answers_when_the_catalogue_is_empty():
    """The point of the above: empty is a usable answer, not a dead end."""
    assert default_voice_for("es", ()) == FALLBACK_VOICE_BY_LANGUAGE["es"]
