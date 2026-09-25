#!/usr/bin/env python3
"""A stdio MCP server that stands in for Lemma's tools while recording.

It exists so a recorded transcript shows exactly how each agent names, frames
and returns a *Lemma* MCP tool call -- the case the normalizers must tell apart
from the agent's own native tools. It answers the way the real conversation MCP
endpoint does: the result is a JSON object, carried both as `structuredContent`
and as the text of a single content block.
"""

import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

TOOLS = [
    {
        "name": "lemma_exec_command",
        "description": "Run a shell command in the Lemma workspace sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    },
    {
        "name": "lemma_display_resource",
        "description": "Show a resource to the person in the Lemma conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["type", "content"],
        },
    },
]


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def answer(request_id, value):
    emit({"jsonrpc": "2.0", "id": request_id, "result": value})


def tool_result(name, arguments):
    if name == "lemma_exec_command":
        command = str(arguments.get("cmd", ""))
        output = command.removeprefix("echo ").strip() + "\n"
        return {"exit_code": 0, "stdout": output, "stderr": ""}
    if name == "lemma_display_resource":
        return {"success": True, "resource_id": "resource-1"}
    return {"success": False, "error": f"unknown tool {name}"}


for raw_line in sys.stdin:
    if not raw_line.strip():
        continue
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        answer(
            request_id,
            {
                "protocolVersion": message.get("params", {}).get(
                    "protocolVersion", "2025-06-18"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lemma_tools", "version": "1.0.0"},
            },
        )
    elif method == "tools/list":
        answer(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params", {})
        value = tool_result(params.get("name"), params.get("arguments") or {})
        answer(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value,
                "isError": False,
            },
        )
    elif method == "ping":
        answer(request_id, {})
    else:
        emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            }
        )
