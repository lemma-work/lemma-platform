"""Platform markup, read into text and the people and links it names."""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.markup import (
    Link,
    Named,
    markup_of,
    slack_markup,
    teams_markup,
    telegram_markup,
    whatsapp_markup,
)


def test_slack_mentions_links_channels_and_broadcasts():
    markup = slack_markup(
        "ask <@U1> or <@UBOT|lem> about <https://x.test/a?b=1&amp;c=2|the doc>"
        " in <#C1|general>, <!here> &lt;3 <mailto:a@b.test|a@b.test>",
        bot_ids=frozenset({"UBOT"}),
    )
    assert markup.segments == (
        "ask ",
        Named(external_id="U1"),
        " or ",
        Named(external_id="UBOT", label="lem", is_bot=True),
        " about ",
        Link(url="https://x.test/a?b=1&c=2", label="the doc"),
        " in #general, @here <3 a@b.test",
    )


def test_slack_link_without_label_and_unknown_specials():
    markup = slack_markup(
        "see <https://x.test> <!subteam^S1|@eng> <!date^1|today> <#C9>"
    )
    assert markup.segments == (
        "see ",
        Link(url="https://x.test"),
        " @eng today #C9",
    )


def test_text_without_markup_is_one_plain_segment():
    assert slack_markup("nothing to see").is_plain
    assert whatsapp_markup("call me at 5").is_plain


def test_teams_at_tags_are_matched_to_their_entities():
    activity = {
        "text": "<p><at>Lemma</at>&nbsp;ask <at>Priya Shah</at> about "
        '<a href="https://x.test">the plan</a></p>',
        "recipient": {"id": "28:bot"},
        "entities": [
            {
                "type": "mention",
                "text": "<at>Lemma</at>",
                "mentioned": {"id": "28:bot", "name": "Lemma"},
            },
            {
                "type": "mention",
                "text": "<at>Priya Shah</at>",
                "mentioned": {
                    "id": "29:priya",
                    "name": "Priya Shah",
                    "aadObjectId": "aad-priya",
                },
            },
        ],
    }
    parsed_text = "Lemma&nbsp;ask Priya Shah about the plan"
    markup = teams_markup(parsed_text + "\n\n[file]", activity=activity)
    assert markup.segments == (
        Named(external_id="28:bot", label="Lemma", is_bot=True),
        " ask ",
        Named(external_id="aad-priya", label="Priya Shah"),
        " about ",
        Link(url="https://x.test", label="the plan"),
        "\n\n[file]",
    )


def test_teams_text_that_is_not_the_activity_is_left_alone():
    activity = {"text": "<at>Lemma</at> hi", "entities": []}
    assert teams_markup("a transcript", activity=activity).segments == ("a transcript",)


def test_teams_at_tag_with_no_entity_is_its_name():
    activity = {"text": "<at>Somebody</at> hi"}
    assert teams_markup("Somebody hi", activity=activity).segments == ("Somebody hi",)


def test_telegram_entities_by_utf16_offset():
    text = "🎉 hey Bob, see the docs"
    update = {
        "message": {
            "text": text,
            "entities": [
                # The emoji is two UTF-16 units.
                {"type": "text_mention", "offset": 7, "length": 3, "user": {"id": 42}},
                {
                    "type": "text_link",
                    "offset": 20,
                    "length": 4,
                    "url": "https://docs.test",
                },
            ],
        }
    }
    assert telegram_markup(text, update=update).segments == (
        "🎉 hey ",
        Named(external_id="42", label="Bob"),
        ", see the ",
        Link(url="https://docs.test", label="docs"),
    )


def test_telegram_batch_text_is_not_read_by_one_messages_offsets():
    update = {
        "message": {
            "text": "first",
            "entities": [{"type": "url", "offset": 0, "length": 5}],
        }
    }
    assert telegram_markup("first\nsecond", update=update).is_plain


def test_whatsapp_numbers_after_an_at():
    assert whatsapp_markup("@15551234567 and a@b.test").segments == (
        Named(external_id="15551234567"),
        " and a@b.test",
    )


def test_markup_of_reads_what_was_made_of_the_message():
    parsed = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_thread_id="1.1",
        message_text="hi <@U1>",
        raw_payload={"authorizations": [{"user_id": "U1", "is_bot": True}]},
    )
    assert markup_of(parsed).named == (Named(external_id="U1", is_bot=True),)
    assert markup_of(parsed, text="a voice note's words").is_plain
