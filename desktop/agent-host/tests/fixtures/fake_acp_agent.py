#!/usr/bin/env python3
"""Deterministic ACP v1 agent used by the Rust integration suite."""

import json
import pathlib
import sys


log_path = pathlib.Path(sys.argv[1])


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id, value):
    emit({"jsonrpc": "2.0", "id": request_id, "result": value})


def record(message):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, separators=(",", ":")) + "\n")


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    record(message)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        result(
            request_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": False,
                    "mcpCapabilities": {"http": True, "sse": True},
                    "promptCapabilities": {"image": False, "audio": False},
                },
                "authMethods": [],
                "agentInfo": {"name": "fake-acp", "version": "1.0.0"},
            },
        )
    elif method == "session/new":
        result(
            request_id,
            {
                "sessionId": "fake-session",
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "description": "Dynamic model catalog",
                        "category": "model",
                        "type": "select",
                        "currentValue": "fake-1",
                        "options": [
                            {"value": "fake-1", "name": "Fake 1"},
                            {"value": "fake-2", "name": "Fake 2"},
                        ],
                    }
                ],
            },
        )
    elif method == "session/set_config_option":
        result(
            request_id,
            {
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "category": "model",
                        "type": "select",
                        "currentValue": message["params"]["value"],
                        "options": [{"value": "fake-2", "name": "Fake 2"}],
                    }
                ]
            },
        )
    elif method == "session/prompt":
        emit(
            {
                "jsonrpc": "2.0",
                "id": 900,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fake-session",
                    "toolCall": {
                        "toolCallId": "native-shell",
                        "status": "pending",
                        "title": "Run native shell",
                    },
                    "options": [
                        {
                            "optionId": "allow_once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        }
                    ],
                },
            }
        )
        permission_response = json.loads(sys.stdin.readline())
        record(permission_response)
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "thinking"},
                    },
                },
            }
        )
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello from fake"},
                    },
                },
            }
        )
        result(request_id, {"stopReason": "end_turn"})
    else:
        result(
            request_id,
            {
                "error": {
                    "code": -32601,
                    "message": f"unsupported method {method}",
                }
            },
        )
