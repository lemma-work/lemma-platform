"""Voices come from the provider, and a provider that cannot answer is not fatal.

The catalogue replaced a hand-written dict of one voice per language. Aura-2
ships roughly eighty across seven, so the dict was a slice — and a slice that
goes stale on its own, which the provider's 400-on-a-retired-voice fallback
existed to survive. While this was written, two of Deepgram's own documentation
pages disagreed about which languages are covered; the API does not.
"""

from __future__ import annotations

import pytest

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
