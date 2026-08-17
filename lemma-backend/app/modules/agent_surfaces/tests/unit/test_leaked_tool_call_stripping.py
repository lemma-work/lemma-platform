"""A tool call the model wrote as text must not reach the user.

QA reproduced this on a real pod: asked an agent for a row count and got

    {"tool_name":"pod_query","args":{...}}6

rendered into the chat. The trailing ``6`` is the answer; the envelope in front
of it is the model narrating a call the harness had already made on its own.
Nothing downstream could tell the two apart, because both arrive as assistant
text on the content channel.

The stripping is deliberately narrow, and half of these tests are about what it
must *not* touch. Deleting a real answer is worse than leaking a malformed one,
so an answer that merely contains or discusses JSON has to survive intact.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.rendering import (
    sanitize_user_visible_text,
    strip_leaked_tool_calls,
)

pytestmark = pytest.mark.unit


# -- what must be stripped ---------------------------------------------------


def test_the_reported_leak_leaves_only_the_answer():
    text = '{"tool_name":"pod_query","args":{"sql":"select count(*) from tasks"}}6'

    assert strip_leaked_tool_calls(text) == "6"


@pytest.mark.parametrize(
    "envelope",
    [
        '{"tool_name":"q","args":{}}',
        '{"tool_name":"q","arguments":{}}',
        '{"name":"q","arguments":{}}',
        '{"tool":"q","parameters":{}}',
        '{"function":"q","arguments":{}}',
        '{"id":"call_1","name":"q","arguments":{}}',
    ],
    ids=["tool_name+args", "tool_name+arguments", "openai", "tool+parameters",
         "function", "with-id"],
)
def test_every_known_envelope_spelling_is_stripped(envelope):
    """Different OpenAI-compatible endpoints spell this differently."""
    assert strip_leaked_tool_calls(f"{envelope} the answer") == "the answer"


def test_several_stacked_envelopes_are_all_stripped():
    """A model that narrates two calls in a row leaks two objects."""
    text = '{"tool_name":"a","args":{}}{"tool_name":"b","args":{}}done'

    assert strip_leaked_tool_calls(text) == "done"


def test_a_brace_inside_a_string_value_does_not_end_the_object_early():
    """Scanned by depth outside strings, not by regex, for exactly this."""
    text = '{"tool_name":"q","args":{"sql":"select \'}\' from t"}}42'

    assert strip_leaked_tool_calls(text) == "42"


def test_reasoning_and_a_leaked_call_are_both_removed():
    """The two strippers compose at the user-visible boundary."""
    text = '<think>I should query</think>{"tool_name":"q","args":{}}6'

    assert sanitize_user_visible_text(text) == "6"


# -- what must survive -------------------------------------------------------


def test_a_json_answer_is_not_mistaken_for_an_envelope():
    """An agent asked for JSON must get its answer delivered."""
    text = '{"total": 6, "unit": "rows"}'

    assert strip_leaked_tool_calls(text) == text


def test_an_envelope_shaped_object_that_is_not_leading_is_left_alone():
    """Only a *leading* object is a leak; mid-sentence it is content."""
    text = 'The call I made was {"tool_name":"x","args":{}} — clear?'

    assert strip_leaked_tool_calls(text) == text


def test_an_object_with_extra_keys_is_content_not_an_envelope():
    """Keys must match a known pair exactly, so a record is never eaten."""
    text = '{"tool_name":"q","args":{},"rows":6,"elapsed_ms":12}'

    assert strip_leaked_tool_calls(text) == text


def test_malformed_json_is_left_alone():
    """Half a JSON object is not something to guess about."""
    text = '{"tool_name":"q","args":{'

    assert strip_leaked_tool_calls(text) == text


@pytest.mark.parametrize("text", ["", "   ", "6", "Hello there."])
def test_ordinary_text_passes_through(text):
    assert strip_leaked_tool_calls(text) == text.strip()


def test_a_reply_that_is_only_a_leaked_call_becomes_empty():
    """Which the observer turns into a real message rather than silence.

    `_assistant_text_was_all_reasoning` covers this case too, so delivery says
    the turn produced nothing instead of sending an empty string.
    """
    assert strip_leaked_tool_calls('{"tool_name":"q","args":{}}') == ""
