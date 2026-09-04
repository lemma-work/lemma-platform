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


def script_thinking(thinking: str, text: str) -> ScriptTurn:
    """A turn that reasons and then answers, the way a reasoning model does.

    The thought is delivered as its own part. For the *other* shape -- a model
    that writes its reasoning into the answer as ``<think>`` tags -- use
    ``script_inline_reasoning``: the two are different failure surfaces and a
    test that means one should not accidentally get the other.
    """
    return {"thinking": thinking, "text": text, "tool_calls": []}


def script_inline_reasoning(reasoning: str, answer: str = "") -> ScriptTurn:
    """A turn whose *answer* has reasoning inlined in it as tags.

    What Fireworks-class models do once a conversation has taught them to, and
    the shape that reached users as an ordinary assistant message. Built from
    ordinals so the tags survive tooling that reads source as markup, and left
    for the mock to chunk so the opening tag straddles a delta boundary exactly
    as it does in production -- which is the specific reason pydantic-ai's own
    tag handling misses it.

    Pass ``answer=""`` for the case with no answer at all behind the reasoning.
    """
    open_tag = chr(60) + "think" + chr(62)
    close_tag = chr(60) + "/think" + chr(62)
    body = f"{open_tag}\n{reasoning}\n{close_tag}"
    return script_text(f"{body}\n\n{answer}" if answer else body)


def with_usage(
    turn: ScriptTurn,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> ScriptTurn:
    """Declare the token counts this turn should report.

    Without it the mock reports pydantic-ai's estimate -- ~50 input tokens per
    request and never a cached one -- which is enough to prove a usage row got
    written and not enough to assert what it cost. Anything checking a price, a
    cached-input discount or a spend limit needs the numbers pinned here.

    ``input_tokens`` is the inclusive total: the cache counts are subsets of it,
    the way every provider reports them.
    """
    return {
        **turn,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
    }


def script_tool_result_ref(tool_call_id: str, path: str) -> str:
    """Refer to a field of an earlier tool call's result.

    A script is static JSON, so it can only pass literals - but ids the
    workspace mints at runtime (a process id, say) are not knowable when the
    script is written. Pass this instead of inventing one: a made-up id proves
    nothing, because the tool correctly reports that no such thing exists.

        script_tool_call("manage_process", {
            "action": "input",
            "process_id": script_tool_result_ref("shell-tty-1", "process_id"),
        })
    """
    return f"${{{tool_call_id}.{path}}}"


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
    # Match how pydantic-ai actually persists an `ask_user(ctx, request:
    # AskUserRequest)` call: it FLATTENS the single pydantic-model parameter, so
    # the stored tool args are the model's fields — `{"questions": [...]}` — NOT
    # `{"request": {"questions": [...]}}`. Emitting the wrapped shape here (as this
    # helper used to) hid a production swallow where the surface read
    # tool_args["request"] and found nothing. Keep this flat so the e2e matrix
    # exercises the real shape.
    return script_tool_call(
        "ask_user",
        {"questions": questions},
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
    "with_usage",
    "ScriptTurn",
    "script_ask_user",
    "script_display_resource",
    "script_email_reply",
    "script_inline_reasoning",
    "script_model_error",
    "script_progress",
    "script_request_approval",
    "script_say",
    "script_text",
    "script_thinking",
    "script_tool_call",
]
