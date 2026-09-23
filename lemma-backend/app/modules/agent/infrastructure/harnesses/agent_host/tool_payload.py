"""Reading an ACP tool-call payload safely.

Adapters vary in where they put a tool call's name, arguments, and result, so
these probe several shapes rather than assuming one. ``bounded_tool_value`` is
the guard that keeps a pathological *result* — a megabyte of stdout, a deeply
nested object — from being persisted verbatim into a conversation message.

That bounding is lossy on purpose, so anything that must survive intact (a
structured final answer, say) has to be read from the raw payload *before* it
passes through here.

Arguments are deliberately exempt: see :func:`unbounded_tool_value`.
"""

from __future__ import annotations

import json
import shlex

from app.modules.agent.domain.value_objects import JsonObject, JsonValue
from app.modules.agent.infrastructure.mcp import normalize_local_mcp_tool_name

_MAX_TOOL_STRING_CHARACTERS = 4_096
_MAX_TOOL_COLLECTION_ITEMS = 32
_MAX_TOOL_VALUE_DEPTH = 4


def first_present(payload: JsonObject, *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def tool_name_from_payload(payload: JsonObject) -> str:
    """The tool's own name, in the spelling the rest of Lemma uses.

    ACP's ``ToolCall`` has no required name field, so the name has to be found
    among several optional ones — and the answer decides how the call renders,
    because every label, icon and contextual card is keyed on the tool name.

    ``_meta`` is checked before ``kind`` because ``kind`` is a *category*, not
    an identity: every web search, page fetch and MCP call an agent makes
    arrives as ``fetch`` / ``other``, so reading it as the name collapsed
    unrelated tools into one and lost the only word worth showing. Claude Code
    reports the real name under ``_meta.claudeCode.toolName``, and this reads
    any vendor's ``_meta.<vendor>.toolName`` rather than that one convention.

    The result is normalized, so a Lemma MCP tool a local agent calls as
    ``mcp__lemma__lemma_exec_command`` is the same ``exec_command`` the pod
    agent calls directly. Third-party MCP names are left alone.
    """
    return normalize_local_mcp_tool_name(_reported_tool_name(payload))


def _reported_tool_name(payload: JsonObject) -> str:
    for key in ("name", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta_name = _meta_tool_name(payload.get("_meta"))
    if meta_name:
        return meta_name
    kind = payload.get("kind")
    if isinstance(kind, str) and kind.strip():
        normalized = kind.strip().lower()
        # "other" is the ACP kind for everything an adapter has no category
        # for — every MCP tool included. It names nothing, so the title (which
        # for those calls *is* the tool name) is the better answer.
        if normalized != "other":
            return _KIND_NAMES.get(normalized, normalized)
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "tool"


#: ACP's categories, in the names Lemma's own tools use -- every label, icon and
#: card is keyed on the name, so a host agent's read renders as a read.
_KIND_NAMES = {
    "execute": "exec_command",
    "read": "read_file",
    "edit": "edit_file",
    "delete": "delete_file",
    "move": "move_file",
    "search": "grep",
    "fetch": "web_fetch",
}


def _meta_tool_name(meta: object) -> str | None:
    """Read ``_meta.<vendor>.toolName`` from an ACP payload's extension point."""
    if not isinstance(meta, dict):
        return None
    for value in meta.values():
        if not isinstance(value, dict):
            continue
        for key in ("toolName", "tool_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def tool_metadata(metadata: JsonObject, payload: JsonObject) -> JsonObject:
    result = dict(metadata)
    for source, target in (("title", "tool_title"), ("kind", "tool_kind")):
        value = payload.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value.strip()
    return result


def raw_tool_args(payload: JsonObject) -> object:
    """The arguments this payload reports, before any normalisation.

    Separate from :func:`tool_args` because merging successive ACP updates has
    to compare what each one *carried*, and cannot do that against a value that
    has already been rewritten for one tool's spelling.
    """
    return first_present(payload, "arguments", "args", "rawInput")


def tool_args(payload: JsonObject, tool_name: str) -> JsonValue:
    value = raw_tool_args(payload)
    if tool_name == "exec_command":
        value = _command_args(value, payload)
    elif tool_name in _FILE_TOOLS:
        value = _file_args(value, payload)
    return unbounded_tool_value(value)


#: Tools whose card is labelled by the file they touched.
_FILE_TOOLS = frozenset({"read_file", "edit_file", "delete_file", "move_file"})

#: Shells whose `-c` script is the command a person would recognise.
_SHELLS = frozenset(
    {"bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh", "/usr/bin/bash"}
)


def _command_args(value: object, payload: JsonObject) -> object:
    """A command, as the text a terminal card shows: `cmd` is a string.

    Adapters send `command` as argv -- Codex's is `["bash", "-lc", "pwd"]` --
    and the card only reads a string, so every such call rendered as a bare
    "Terminal command". A shell's `-c` script is the command; any other argv is
    joined as it would be typed. An adapter that sends no command at all still
    titles the call with it, so the title is the last resort.
    """
    arguments = dict(value) if isinstance(value, dict) else {}
    command = arguments.pop("command", None)
    if "cmd" not in arguments:
        text = _command_text(command)
        if text is None:
            title = payload.get("title")
            text = title.strip().strip("`").strip() if isinstance(title, str) else None
        if text:
            arguments["cmd"] = text
    elif command is not None:
        arguments["command"] = command
    return arguments if arguments or value is not None else value


def _command_text(command: object) -> str | None:
    if isinstance(command, str):
        return command.strip() or None
    if not isinstance(command, list) or not all(
        isinstance(part, str) for part in command
    ):
        return None
    if (
        len(command) >= 3
        and command[0] in _SHELLS
        and command[1] in ("-c", "-lc", "-lic", "-ic")
    ):
        return command[2].strip() or None
    return shlex.join(command) or None


def _file_args(value: object, payload: JsonObject) -> object:
    """A file tool's arguments, with its path where the card looks for one.

    ACP reports the files a call touched in `locations`, which adapters fill even
    when their own arguments name the file differently or not at all.
    """
    arguments = dict(value) if isinstance(value, dict) else {}
    if not any(
        key in arguments for key in ("file_path", "path", "filepath", "target_file")
    ):
        locations = payload.get("locations")
        if isinstance(locations, list):
            for location in locations:
                path = location.get("path") if isinstance(location, dict) else None
                if isinstance(path, str) and path:
                    arguments["file_path"] = path
                    break
    return arguments if arguments or value is not None else value


def tool_result(tool_name: str, status: str, payload: JsonObject) -> JsonValue:
    """What a finished call returned, in the shape Lemma's own tools return.

    A terminal card reads `exit_code`, `stdout` and `stderr`. A failure keeps
    its output: replacing the result with `{"success": false}` is what left a
    failed command showing only the word "failed", when what a person needed
    was the error the command printed.
    """
    raw = first_present(payload, "result", "rawOutput")
    if tool_name == "exec_command":
        result: JsonObject = _command_result(raw, payload)
    else:
        unwrapped = bounded_tool_value(unwrap_mcp_content(raw))
        if status == "COMPLETED":
            return unwrapped
        result = {"output": unwrapped} if unwrapped not in (None, "", {}, []) else {}
    if status != "COMPLETED":
        result["success"] = False
        result["error"] = _failure_sentence(status, payload, result)
    return result


def _command_result(raw: object, payload: JsonObject) -> JsonObject:
    fields = raw if isinstance(raw, dict) else {}
    result: JsonObject = {}
    exit_code = first_present(fields, "exit_code", "exitCode")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        result["exit_code"] = exit_code
    stdout = first_present(
        fields, "stdout", "aggregated_output", "formatted_output", "output"
    )
    if not isinstance(stdout, str):
        stdout = raw if isinstance(raw, str) else _content_text(payload)
    if stdout:
        result["stdout"] = bounded_tool_value(stdout)
    stderr = fields.get("stderr")
    if isinstance(stderr, str) and stderr:
        result["stderr"] = bounded_tool_value(stderr)
    return result


def _content_text(payload: JsonObject) -> str:
    """Text an adapter reported as ACP `content` blocks rather than `rawOutput`."""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        block = item.get("content") if isinstance(item, dict) else None
        text = block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _failure_sentence(status: str, payload: JsonObject, result: JsonObject) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int):
        return f"exited with code {exit_code}"
    return {"DENIED": "not allowed", "CANCELLED": "cancelled"}.get(
        status, status.lower()
    )


def unbounded_tool_value(value: object) -> JsonValue:
    """A tool's arguments, coerced to JSON-safe types but never truncated.

    Bounding a *result* is a guard against a megabyte of stdout. Bounding the
    *arguments* is data loss: they are what the conversation renders and what a
    view is built from. ``display_resource(type="WIDGET")`` carries its whole
    HTML document in ``content``, far past ``_MAX_TOOL_STRING_CHARACTERS``, so
    the bound replaced the widget with a placeholder and left the card nothing
    to render. The in-process harness stores arguments unbounded; this is the
    same promise, so a conversation reads the same either way.
    """
    if isinstance(value, dict):
        return {str(key): unbounded_tool_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unbounded_tool_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def bounded_tool_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth >= _MAX_TOOL_VALUE_DEPTH:
        return {"omitted": "nested tool payload"}
    if isinstance(value, str):
        if len(value) <= _MAX_TOOL_STRING_CHARACTERS:
            return value
        # Keep the head. This replaced the whole string, so a 4,097-character
        # result lost all 4,097 -- and because this payload *is* the persisted
        # transcript, a `carries_history=True` resume replayed
        # `{"omitted": ...}` where the file the agent had read used to be. The
        # collection branches below already report what they dropped; this one
        # did not even keep a sample.
        return {
            "truncated": True,
            "character_count": len(value),
            "preview": value[:_MAX_TOOL_STRING_CHARACTERS],
        }
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): bounded_tool_value(item, depth=depth + 1)
            for key, item in items[:_MAX_TOOL_COLLECTION_ITEMS]
        }
        if len(items) > _MAX_TOOL_COLLECTION_ITEMS:
            result["_omitted_item_count"] = len(items) - _MAX_TOOL_COLLECTION_ITEMS
        return result
    if isinstance(value, list):
        result = [
            bounded_tool_value(item, depth=depth + 1)
            for item in value[:_MAX_TOOL_COLLECTION_ITEMS]
        ]
        if len(value) > _MAX_TOOL_COLLECTION_ITEMS:
            result.append(
                {"omitted_item_count": len(value) - _MAX_TOOL_COLLECTION_ITEMS}
            )
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def unwrap_mcp_content(value: object) -> object:
    """An MCP tool's own return value, out of the content blocks carrying it.

    An adapter reports an MCP call's ``rawOutput`` as the MCP envelope — a list
    of content blocks — while the in-process harness stores what the tool
    actually returned. That difference is not cosmetic: the frontend reads a
    result as an object, so an envelope arrives as ``{"output": [...]}`` and
    every field the caller wanted (a served view's ``url``, a ``success`` flag)
    is a level too deep to find.

    Only the unambiguous case is unwrapped — a single text block whose text is
    a JSON object. Anything else is the tool's answer as given: a genuinely
    multi-part result is not an envelope around one value, and guessing which
    part was meant would lose the rest.
    """
    blocks = value.get("content") if isinstance(value, dict) else value
    if not isinstance(blocks, list) or len(blocks) != 1:
        return value
    block = blocks[0]
    if not isinstance(block, dict) or block.get("type") != "text":
        return value
    text = block.get("text")
    if not isinstance(text, str):
        return value
    try:
        decoded = json.loads(text)
    except TypeError, ValueError:
        return value
    return decoded if isinstance(decoded, dict) else value


def json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}
