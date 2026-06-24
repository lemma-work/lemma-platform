"""Deterministic guards for the Telegram safe-render send path (Bug D).

Covers the MarkdownV2 renderer/chunker and TelegramPlatformService.send_message:
escaped MarkdownV2, 4096 chunking, plain-text fallback on parse-entity errors,
reply_parameters, and forum-topic threading.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.rendering import (
    chunk_text,
    escape_markdown_v2,
    strip_thinking_tokens,
    to_markdown_v2,
)
from app.modules.agent_surfaces.platforms.telegram.client import TelegramApiError
from app.modules.agent_surfaces.platforms.telegram.parser import TelegramMessageParser
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)


# --- renderer -------------------------------------------------------------

def test_escape_markdown_v2_escapes_reserved_characters():
    assert escape_markdown_v2("a.b!c-d") == "a\\.b\\!c\\-d"


def test_to_markdown_v2_converts_common_formatting():
    assert to_markdown_v2("**bold**") == "*bold*"
    assert to_markdown_v2("*italic*") == "_italic_"
    assert to_markdown_v2("~~gone~~") == "~gone~"
    # Parentheses inside inline code are not escaped (code-entity rules).
    assert to_markdown_v2("`f(x)`") == "`f(x)`"
    # Link text is escaped; only ) and \\ are escaped in the URL.
    assert to_markdown_v2("[a.b](http://x/y_z)") == "[a\\.b](http://x/y_z)"


def test_to_markdown_v2_escapes_plain_specials():
    assert to_markdown_v2("price (50%)!") == "price \\(50%\\)\\!"


def test_chunk_text_respects_limit_and_prefers_boundaries():
    text = "para one\n\n" + ("x" * 50)
    chunks = chunk_text(text, limit=20)
    assert all(len(c) <= 20 for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks).startswith("paraone")


def test_chunk_text_hard_splits_single_long_run():
    chunks = chunk_text("a" * 100, limit=40)
    assert [len(c) for c in chunks] == [40, 40, 20]


# --- send_message ---------------------------------------------------------

class _RecordingClient:
    """Stand-in for TelegramClient.call that records payloads and can fail."""

    def __init__(self, *, fail_parse_on_markdown: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_parse_on_markdown = fail_parse_on_markdown

    async def __call__(self, method, payload, client=None):
        self.calls.append((method, payload))
        if self.fail_parse_on_markdown and payload.get("parse_mode") == "MarkdownV2":
            raise TelegramApiError(
                method=method,
                status_code=400,
                description="Bad Request: can't parse entities: oops",
            )
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _service(recorder: _RecordingClient) -> TelegramPlatformService:
    service = TelegramPlatformService({"bot_token": "test-token"})
    service._client.call = recorder  # type: ignore[assignment]
    return service


def _event(text: str = "hi", *, message_id: int = 5, thread_id: int | None = None):
    message: dict = {
        "message_id": message_id,
        "chat": {"id": 123, "type": "supergroup" if thread_id else "private"},
        "from": {"id": 1, "first_name": "U"},
        "text": text,
    }
    if thread_id is not None:
        message["message_thread_id"] = thread_id
        message["is_topic_message"] = True
    parsed = TelegramMessageParser().parse({"message": message})
    assert parsed is not None
    return parsed


async def test_send_message_uses_markdown_v2_and_reply_parameters():
    recorder = _RecordingClient()
    service = _service(recorder)

    await service.send_message(_event(), "Use **bold** here.")

    assert len(recorder.calls) == 1
    method, payload = recorder.calls[0]
    assert method == "sendMessage"
    assert payload["parse_mode"] == "MarkdownV2"
    assert payload["text"] == "Use *bold* here\\."
    assert payload["chat_id"] == "123"
    assert payload["reply_parameters"] == {
        "message_id": "5",
        "allow_sending_without_reply": True,
    }
    assert "message_thread_id" not in payload


async def test_send_message_falls_back_to_plain_text_on_parse_error():
    recorder = _RecordingClient(fail_parse_on_markdown=True)
    service = _service(recorder)

    await service.send_message(_event(), "weird _markdown_ text")

    assert len(recorder.calls) == 2
    first_method, first_payload = recorder.calls[0]
    second_method, second_payload = recorder.calls[1]
    assert first_payload["parse_mode"] == "MarkdownV2"
    # Fallback drops parse_mode and sends the raw text verbatim.
    assert "parse_mode" not in second_payload
    assert second_payload["text"] == "weird _markdown_ text"
    assert second_method == "sendMessage"


async def test_send_message_chunks_long_text_under_limit():
    recorder = _RecordingClient()
    service = _service(recorder)

    await service.send_message(_event(), "x" * 9000)

    assert len(recorder.calls) >= 3
    assert all(len(payload["text"]) <= 4096 for _, payload in recorder.calls)
    # Only the first chunk threads the reply.
    assert "reply_parameters" in recorder.calls[0][1]
    assert "reply_parameters" not in recorder.calls[1][1]


async def test_send_message_sets_forum_topic_thread_id():
    recorder = _RecordingClient()
    service = _service(recorder)

    await service.send_message(_event(thread_id=99), "in a topic")

    _, payload = recorder.calls[0]
    assert payload["message_thread_id"] == 99


# --- thinking token stripping -----------------------------------------------

# Construct thinking tags programmatically so they survive in source (writing
# them literally causes the tags to be stripped as markup by editors/tools).
_T_OPEN = chr(60) + "think" + chr(62)
_T_CLOSE = chr(60) + "/think" + chr(62)
_THINKING_OPEN = chr(60) + "thinking" + chr(62)
_THINKING_CLOSE = chr(60) + "/thinking" + chr(62)


def test_strip_thinking_tokens_removes_closed_block():
    text = f"Let me think. {_T_OPEN}I should help the user.{_T_CLOSE} Here is your answer."
    result = strip_thinking_tokens(text)
    assert "<think" not in result.lower()
    assert "I should help the user." not in result
    assert "Let me think." in result
    assert "Here is your answer." in result


def test_strip_thinking_tokens_removes_unclosed_block():
    text = f"Here is your answer. {_T_OPEN}Let me continue thinking"
    result = strip_thinking_tokens(text)
    assert "<think" not in result.lower()
    assert "Let me continue thinking" not in result
    assert "Here is your answer." in result


def test_strip_thinking_tokens_removes_self_closing_tag():
    self_closing = chr(60) + "think/" + chr(62)
    assert strip_thinking_tokens(f"Hello {self_closing} world") == "Hello  world".strip()


def test_strip_thinking_tokens_removes_thinking_variant():
    text = f"Reasoning here {_THINKING_OPEN}internal{_THINKING_CLOSE} Answer"
    result = strip_thinking_tokens(text)
    assert "<thinking" not in result.lower()
    assert "internal" not in result
    assert "Answer" in result


def test_strip_thinking_tokens_handles_attributes():
    open_with_attr = chr(60) + 'think key="val"' + chr(62)
    text = f"Reasoning {open_with_attr}internal{_T_CLOSE} Answer"
    result = strip_thinking_tokens(text)
    assert "<think" not in result.lower()
    assert "internal" not in result
    assert "Answer" in result


def test_strip_thinking_tokens_case_insensitive():
    upper_open = chr(60) + "THINK" + chr(62)
    upper_close = chr(60) + "/THINK" + chr(62)
    text = f"REASONING {upper_open}internal{upper_close} Answer"
    result = strip_thinking_tokens(text)
    assert "<think" not in result.lower()
    assert "internal" not in result
    assert "Answer" in result


def test_strip_thinking_tokens_multiple_blocks():
    text = f"First reasoning {_T_OPEN}r1{_T_CLOSE} Continue {_T_OPEN}r2{_T_CLOSE} Final answer"
    result = strip_thinking_tokens(text)
    assert "<think" not in result.lower()
    assert "r1" not in result
    assert "r2" not in result
    assert "Final answer" in result


def test_strip_thinking_tokens_no_tags_returns_unchanged():
    assert strip_thinking_tokens("Just a normal message") == "Just a normal message"


def test_strip_thinking_tokens_only_thinking_returns_empty():
    assert strip_thinking_tokens(f"{_T_OPEN}All thinking, no answer{_T_CLOSE}") == ""


def test_strip_thinking_tokens_empty_string():
    assert strip_thinking_tokens("") == ""


def test_strip_thinking_tokens_preserves_code_blocks():
    text = 'Check this code:\n```python\nx = 1  # not a think tag\n```\nDone'
    result = strip_thinking_tokens(text)
    assert "```python" in result
    assert "Done" in result
