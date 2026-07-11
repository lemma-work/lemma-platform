"""Shared scripted-turn DSL for the deterministic E2E FunctionModel.

Both agent HTTP journeys and agent-surface webhook journeys persist these turns
under ``mock_llm_script``. Production still runs the real PydanticAI harness,
tools, persistence, streaming, and worker code; this module describes only the
model's deterministic next response.
"""

from __future__ import annotations

from typing import Any

ScriptTurn = dict[str, Any]


def script_text(text: str) -> ScriptTurn:
    return {"text": text, "tool_calls": []}


def script_model_error(
    kind: str,
    *,
    message: str,
    status_code: int | None = None,
) -> ScriptTurn:
    """Raise a deterministic provider-side failure on the next model turn."""
    error: dict[str, Any] = {"kind": kind, "message": message}
    if status_code is not None:
        error["status_code"] = status_code
    return {"error": error}


def script_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    tool_call_id: str | None = None,
    text: str | None = None,
) -> ScriptTurn:
    call: dict[str, Any] = {"tool_name": tool_name, "args": args}
    if tool_call_id is not None:
        call["tool_call_id"] = tool_call_id
    return {"text": text, "tool_calls": [call]}


def script_ask_user(
    questions: list[dict[str, Any]],
    *,
    tool_call_id: str = "tool-ask-1",
    text: str | None = None,
) -> ScriptTurn:
    return script_tool_call(
        "ask_user",
        {"request": {"questions": questions}},
        tool_call_id=tool_call_id,
        text=text,
    )


def script_request_approval(
    *,
    tool_name: str,
    args: dict[str, Any],
    title: str,
    reason: str | None = None,
    tool_call_id: str = "tool-approval-1",
    text: str | None = None,
) -> ScriptTurn:
    call_args: dict[str, Any] = {"tool_name": tool_name, "args": args, "title": title}
    if reason is not None:
        call_args["reason"] = reason
    return script_tool_call(
        "request_approval",
        call_args,
        tool_call_id=tool_call_id,
        text=text,
    )


def script_display_resource(
    *,
    type: str,  # noqa: A002 - matches the real field name
    path: str | None = None,
    name: str | None = None,
    tool_call_id: str = "tool-display-1",
    text: str | None = None,
    **extra: Any,
) -> ScriptTurn:
    request: dict[str, Any] = {"type": type}
    if path is not None:
        request["path"] = path
    if name is not None:
        request["name"] = name
    request.update(extra)
    return script_tool_call(
        "display_resource",
        {"request": request},
        tool_call_id=tool_call_id,
        text=text,
    )


def script_say(
    text_to_speak: str,
    *,
    tool_call_id: str = "tool-say-1",
    voice: str | None = None,
    output_file_path: str | None = None,
    text: str | None = None,
) -> ScriptTurn:
    request: dict[str, Any] = {"text": text_to_speak}
    if voice is not None:
        request["voice"] = voice
    if output_file_path is not None:
        request["output_file_path"] = output_file_path
    return script_tool_call(
        "say",
        {"request": request},
        tool_call_id=tool_call_id,
        text=text,
    )


def script_email_reply(
    tool_name: str,
    content: str,
    *,
    content_type: str = "markdown",
    attachment_paths: list[str] | None = None,
    subject: str | None = None,
    tool_call_id: str = "tool-email-reply-1",
    text: str | None = None,
) -> ScriptTurn:
    request: dict[str, Any] = {"content": content, "content_type": content_type}
    if attachment_paths:
        request["attachment_paths"] = attachment_paths
    if subject is not None:
        request["subject"] = subject
    return script_tool_call(
        tool_name,
        {"request": request},
        tool_call_id=tool_call_id,
        text=text,
    )


def script_progress(
    comments: list[str],
    *,
    final_text: str = "All done.",
    tool_name: str,
) -> list[ScriptTurn]:
    turns = [
        script_tool_call(
            tool_name,
            {"request": {"comment": comment}},
            tool_call_id=f"tool-progress-{index}",
        )
        for index, comment in enumerate(comments)
    ]
    turns.append(script_text(final_text))
    return turns


__all__ = [
    "ScriptTurn",
    "script_ask_user",
    "script_display_resource",
    "script_email_reply",
    "script_model_error",
    "script_progress",
    "script_request_approval",
    "script_say",
    "script_text",
    "script_tool_call",
]
