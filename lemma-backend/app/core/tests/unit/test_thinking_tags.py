"""Finding reasoning a model wrote into its answer.

The convention is a tag, so most of these are parsing cases. The ones that
matter are the two that shipped a bug: a tag split across stream deltas (which
is how Fireworks actually sends it, and which pydantic-ai's own splitter misses
because it compares a delta against the literal ``<think>``), and an unclosed
block (which has no answer in it at all, so treating the remainder as one puts
reasoning in an answer bubble).
"""

from __future__ import annotations

from app.core.text.thinking_tags import (
    ThinkingStreamSplitter,
    has_thinking_tokens,
    split_thinking_segments,
    strip_thinking_tokens,
)

# Built from ordinals so the tags survive tooling that treats source as markup.
OPEN = chr(60) + "think" + chr(62)
CLOSE = chr(60) + "/think" + chr(62)
OPEN_LONG = chr(60) + "thinking" + chr(62)
CLOSE_LONG = chr(60) + "/thinking" + chr(62)
SELF_CLOSING = chr(60) + "think/" + chr(62)


def _stream(deltas: list[str]) -> list[tuple[str, str]]:
    """Feed a stream and coalesce, so a test asserts meaning not chunking."""
    splitter = ThinkingStreamSplitter()
    segments: list[tuple[str, str]] = []
    for delta in deltas:
        segments.extend(splitter.feed(delta))
    segments.extend(splitter.flush())

    merged: list[tuple[str, str]] = []
    for kind, chunk in segments:
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + chunk)
        else:
            merged.append((kind, chunk))
    return merged


# --- whole-string splitting -------------------------------------------------


def test_a_closed_block_separates_from_the_answer():
    segments = split_thinking_segments(f"{OPEN}I should look it up.{CLOSE}\n\nParis.")
    assert segments == [("thinking", "I should look it up."), ("text", "\n\nParis.")]


def test_an_unclosed_block_is_all_reasoning_and_leaves_no_answer():
    """A model that opens a thought and never closes it never answered.

    Reading the remainder as an answer is what put a paragraph of reasoning in
    an answer bubble, so the greedy read is the safe one.
    """
    segments = split_thinking_segments(f"{OPEN}Still working through it")
    assert segments == [("thinking", "Still working through it")]
    assert strip_thinking_tokens(f"{OPEN}Still working through it") == ""


def test_narration_before_a_thought_stays_an_answer():
    segments = split_thinking_segments(f"On it. {OPEN}check the pods{CLOSE} Done.")
    assert segments == [
        ("text", "On it. "),
        ("thinking", "check the pods"),
        ("text", " Done."),
    ]


def test_the_long_spelling_and_mixed_case_are_the_same_convention():
    assert split_thinking_segments(f"{OPEN_LONG}hmm{CLOSE_LONG}Paris.") == [
        ("thinking", "hmm"),
        ("text", "Paris."),
    ]
    upper = OPEN.upper() + "hmm" + CLOSE.upper()
    assert split_thinking_segments(upper + "Paris.") == [
        ("thinking", "hmm"),
        ("text", "Paris."),
    ]


def test_an_empty_thought_becomes_nothing_rather_than_a_blank_message():
    assert split_thinking_segments(f"{SELF_CLOSING}Paris.") == [("text", "Paris.")]
    assert split_thinking_segments(f"{OPEN}   {CLOSE}Paris.") == [("text", "Paris.")]


def test_two_thoughts_stay_two():
    segments = split_thinking_segments(f"{OPEN}one{CLOSE}mid{OPEN}two{CLOSE}end")
    assert segments == [
        ("thinking", "one"),
        ("text", "mid"),
        ("thinking", "two"),
        ("text", "end"),
    ]


def test_a_tag_inside_a_code_fence_is_prose_about_the_convention():
    """An answer explaining the convention is still an answer.

    Rare, but eating it would be a worse bug than the one this module fixes --
    the reader would lose the whole reply and never learn why.
    """
    answer = f"Models emit this:\n\n```\n{OPEN}reasoning{CLOSE}\n```\n\nThat is it."
    assert split_thinking_segments(answer) == [("text", answer)]
    assert strip_thinking_tokens(answer) == answer.strip()


def test_reasoning_that_contains_a_code_fence_is_still_all_reasoning():
    """The dangerous half of the fence rule, and the reason it is narrow.

    The exemption is for a tag that *opens* inside a fence. A thought that
    happens to quote code is still a thought — if the fence inside it ended the
    block, a model drafting code in its head would have that draft delivered as
    the answer, and on a surface that is the leak this module exists to stop.
    """
    fenced_thought = f"{OPEN}\nDrafting:\n```\nsecret = 1\n```\nRight.{CLOSE}\n\nDone."

    assert split_thinking_segments(fenced_thought) == [
        ("thinking", "\nDrafting:\n```\nsecret = 1\n```\nRight."),
        ("text", "\n\nDone."),
    ]
    assert strip_thinking_tokens(fenced_thought) == "Done."


def test_text_without_a_tag_is_returned_whole():
    assert split_thinking_segments("Just an answer.") == [("text", "Just an answer.")]
    assert strip_thinking_tokens("Just an answer.") == "Just an answer."
    assert has_thinking_tokens("Just an answer.") is False
    assert has_thinking_tokens(None) is False
    assert has_thinking_tokens(f"{OPEN}x{CLOSE}") is True


# --- streaming --------------------------------------------------------------


def test_a_tag_split_across_deltas_is_still_a_tag():
    """The case that shipped the bug.

    Fireworks sends the opening tag as three deltas. pydantic-ai only splits
    when a single delta equals the literal tag, so the whole thought landed in
    the answer. Nothing here may depend on where the deltas fall.
    """
    assert _stream(
        ["<", "think", ">", "I should check", " the pods.", CLOSE, "You have 3."]
    ) == [("thinking", "I should check the pods."), ("text", "You have 3.")]


def test_the_tag_arriving_whole_is_the_same_answer():
    assert _stream([OPEN, "reasoning", CLOSE, "Paris."]) == [
        ("thinking", "reasoning"),
        ("text", "Paris."),
    ]


def test_a_tag_glued_to_its_neighbours_is_still_a_tag():
    assert _stream([f"{OPEN}I", " reason.", f"{CLOSE}Paris."]) == [
        ("thinking", "I reason."),
        ("text", "Paris."),
    ]


def test_a_closing_tag_split_across_deltas_releases_the_answer():
    assert _stream([OPEN, "reasoning", "</thi", "nk>", "Paris."]) == [
        ("thinking", "reasoning"),
        ("text", "Paris."),
    ]


def test_a_stream_that_ends_mid_thought_stays_a_thought():
    assert _stream([OPEN, "still going"]) == [("thinking", "still going")]


def test_a_half_written_tag_is_held_back_rather_than_shown():
    """Held-back text is the whole point: a half tag on screen is the leak.

    What is held is the *undecided* part. Once the tag completes, everything
    after it is known to be reasoning and is released as reasoning straight
    away -- waiting for the closing tag would stall the trace for no reason.
    The invariant is only that no character of it is ever released as text.
    """
    splitter = ThinkingStreamSplitter()
    assert splitter.feed("Here it is. <thi") == [("text", "Here it is. ")]
    assert splitter.feed("nk>secret") == [("thinking", "secret")]
    assert splitter.feed(CLOSE) == []


def test_a_stream_with_no_tags_passes_straight_through():
    assert _stream(["Paris ", "is the ", "capital."]) == [
        ("text", "Paris is the capital.")
    ]
