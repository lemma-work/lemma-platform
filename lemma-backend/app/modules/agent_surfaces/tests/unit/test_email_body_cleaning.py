"""What reaches the model when somebody emails an agent.

All three email providers share ``email_common``, so these are the guarantees
that hold for Resend, Gmail and Outlook alike. None of them held before: there
was no quoted-reply trimming anywhere, stylesheets were extracted as text, and
every newline was collapsed, so an HTML email arrived as one unbroken line.
"""

from __future__ import annotations

import base64

import pytest

from app.modules.agent_surfaces.platforms.email_common import (
    decode_email_html,
    email_thread_root,
    inbound_email_text,
    plain_text_from_html,
    strip_quoted_reply,
)

_QUOTED = """Shipped the importer.

On Mon, 3 Feb 2026 at 09:14, Ops <ops@example.com> wrote:
> What did you ship yesterday?
> Anything blocked?
"""


def test_a_stylesheet_is_not_part_of_the_message():
    text = plain_text_from_html(
        "<style>.p1{color:#000;font-family:Helvetica}</style><p>Line one</p>"
    )

    assert "Line one" in text
    assert "color:#000" not in text
    assert "Helvetica" not in text


def test_script_contents_are_not_part_of_the_message():
    text = plain_text_from_html("<script>var x=1;</script><p>Hello</p>")

    assert text.strip() == "Hello"


def test_paragraphs_survive_as_line_breaks():
    """Collapsing every newline turned an HTML email into one wall of text."""
    text = plain_text_from_html("<p>Line one</p><p>Line two</p>")

    assert "Line one" in text and "Line two" in text
    assert "\n" in text


def test_the_quoted_original_is_dropped_from_a_reply():
    trimmed = strip_quoted_reply(_QUOTED)

    assert trimmed == "Shipped the importer."


@pytest.mark.parametrize(
    "marker",
    [
        "-----Original Message-----",
        "________________________________",
    ],
)
def test_each_client_s_quote_header_is_recognised(marker):
    trimmed = strip_quoted_reply(f"My answer.\n\n{marker}\nolder content here")

    assert trimmed == "My answer."


def test_a_reply_that_is_only_a_quote_is_kept_rather_than_emptied():
    """Trimming to nothing would look identical to the person saying nothing.

    Better to hand the agent a quoted message it can reason about than an empty
    one it cannot.
    """
    body = "> What did you ship?\n> Anything blocked?"

    assert strip_quoted_reply(body).strip() != ""


def test_prose_that_merely_mentions_wrote_is_not_treated_as_a_quote():
    body = "She wrote: the report is done, and I agree with it."

    assert strip_quoted_reply(body) == body


def test_a_base64_data_uri_body_is_decoded():
    """Resend returns html as a data URI; untouched it is a base64 blob."""
    html = base64.b64encode(b"<p>Hello there</p>").decode()

    decoded = decode_email_html(f"data:text/html;base64,{html}", "data_uri")

    assert "<p>Hello there</p>" == decoded


def test_a_malformed_data_uri_does_not_lose_the_email():
    raw = "data:text/html;base64,!!!not-base64!!!"

    assert decode_email_html(raw, "data_uri") != ""


def test_plain_text_is_preferred_over_html():
    text = inbound_email_text(text="the real body", html="<p>ignored</p>")

    assert text == "the real body"


def test_the_thread_root_is_the_first_reference():
    """This is what lets a seeded outbound be recognised when it comes back."""
    root = email_thread_root(
        references=["<seed@ops.asur.work>", "<generated@resend.dev>"],
        in_reply_to="<generated@resend.dev>",
        message_id="<reply@example.com>",
        sender="bob@example.com",
    )

    assert root == "<seed@ops.asur.work>"


def test_a_first_contact_is_its_own_thread_root():
    root = email_thread_root(
        references=[], in_reply_to=None, message_id="<first@example.com>", sender="b@x"
    )

    assert root == "<first@example.com>"


def test_a_pasted_log_followed_by_a_question_is_not_truncated():
    """Pasting output and then asking about it is a core thing people do.

    Cutting at the first `>` deleted the actual question and left only the
    preamble, so the agent answered a message it could not see.
    """
    body = "Here is the log:\n> ERROR connection refused\n> retrying\nWhat should I do?"

    assert strip_quoted_reply(body) == body


def test_a_quote_running_to_the_end_is_still_trimmed():
    """The genuine reply case the trimming exists for."""
    assert strip_quoted_reply("Done.\n> earlier question\n> more quote") == "Done."


def test_ordinary_prose_beginning_with_from_survives():
    """`From:` and `Sent from my …` occur mid-message in real mail.

    Both were quote markers and both truncated messages that had not been
    quoted at all.
    """
    assert strip_quoted_reply("Sure.\nSent from my iPhone\n\nPS the PO is 8812") == (
        "Sure.\nSent from my iPhone\n\nPS the PO is 8812"
    )


# ------------------------------------------------------------ forwarded mail


@pytest.mark.parametrize(
    "subject",
    ["Fwd: invoice", "FW: invoice", "fwd:invoice", "Wg: Rechnung", "TR: facture"],
)
def test_a_forward_keeps_everything_below_the_marker(subject):
    """The forwarded content *is* the message.

    Outlook writes `-----Original Message-----` for replies and forwards alike,
    so the marker cannot tell them apart. Trimming on it left "please process
    this" and threw away the invoice — the one thing the agent was asked to act
    on. Trimming now needs evidence of a reply rather than assuming one.
    """
    body = "Please process this:\n\n-----Original Message-----\nInvoice #8812, due Friday"

    assert strip_quoted_reply(body, subject) == body.strip()


@pytest.mark.parametrize(
    "marker",
    ["---------- Forwarded message ---------", "Begin forwarded message:"],
)
def test_an_explicit_forward_marker_is_enough_without_the_subject(marker):
    """Gmail and Apple Mail say so in the body; the subject may be edited away."""
    body = f"Can you handle this?\n\n{marker}\nFrom: supplier\nPO 8812 confirmed"

    assert strip_quoted_reply(body) == body.strip()


def test_a_reply_is_still_trimmed_when_the_subject_says_re():
    """The forward rule must not disarm trimming for the case it exists for."""
    body = "Shipped the importer.\n\nOn Mon, Ops wrote:\n> What did you ship?"

    assert strip_quoted_reply(body, "Re: Standup") == "Shipped the importer."


def test_a_subject_merely_containing_fwd_is_not_a_forward():
    """Anchored to the start, so "Re: Fwd: thing" — a reply to a forward — trims."""
    body = "Looks right to me.\n\nOn Mon, Ops wrote:\n> see attached"

    assert strip_quoted_reply(body, "Re: Fwd: invoice") == "Looks right to me."
