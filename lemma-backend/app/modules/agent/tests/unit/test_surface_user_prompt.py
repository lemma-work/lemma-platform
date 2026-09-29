"""What the model reads for one message that arrived through a surface.

The standing platform fragment says how delivery works; this is the per-message
half: who sent it, and what they attached. It carries no delivery instructions
of its own -- repeating the email one-reply rule on every message duplicated the
standing guidance, and the attachment listing no longer names download tools
that do not exist.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    user_prompt_text,
)


def _message(text: str, **metadata: object) -> SimpleNamespace:
    return SimpleNamespace(text=text, metadata=metadata)


def test_an_email_message_carries_no_delivery_instruction_of_its_own() -> None:
    """The one-reply rule is standing guidance, said once in the system prompt."""
    prompt = user_prompt_text(
        _message("hello", surface_platform="RESEND", sender_email="a@example.com")
    )
    assert prompt == "[RESEND | a@example.com]:\n\nhello"
    assert "exactly one" not in prompt


def test_unlisted_attachments_are_named_without_a_download_hint() -> None:
    prompt = user_prompt_text(
        _message(
            "see attached",
            surface_platform="SLACK",
            sender_display_name="Ada",
            attachments=[
                {
                    "id": "F1",
                    "name": "q3.pdf",
                    "content_type": "application/pdf",
                    "download_url": "https://files.example/F1",
                }
            ],
        )
    )
    assert "Files attached to this Slack message:" in prompt
    assert "- q3.pdf | application/pdf | id=F1 | download_url=" in prompt
    assert "_download_file" not in prompt


def test_attachments_the_agent_cannot_describe_still_leave_a_count() -> None:
    prompt = user_prompt_text(
        _message("x", surface_platform="SLACK", attachments=[{}, 3])
    )
    assert prompt.endswith("Attachments: 2")


def test_a_telegram_group_message_says_so() -> None:
    """What `telegram_get_current_chat` used to be called to find out."""
    group = user_prompt_text(
        _message(
            "hi",
            surface_platform="TELEGRAM",
            sender_display_name="Ada",
            chat_type="supergroup",
        )
    )
    dm = user_prompt_text(
        _message(
            "hi",
            surface_platform="TELEGRAM",
            sender_display_name="Ada",
            chat_type="private",
        )
    )
    assert group.startswith("[TELEGRAM | Ada | group chat]:")
    assert dm.startswith("[TELEGRAM | Ada]:")
