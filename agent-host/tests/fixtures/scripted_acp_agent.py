#!/usr/bin/env python3
"""An ACP v1 agent that exercises what `fake_acp_agent.py` only pretends to do.

`fake_acp_agent.py` answers the handshake and streams a canned reply. This one
uses what the Agent Host actually hands it:

* ``mcp`` mode launches the ``mcpServers`` entry from ``session/new`` as a
  subprocess and speaks MCP JSON-RPC over its stdio, exactly as Codex or Claude
  Code would, so the Lemma MCP bridge is driven for real.
* ``permission`` mode refuses to finish its turn until a
  ``session/request_permission`` round trip comes back, so the host's parked
  responder and its `RESOLVE_PERMISSION` answer are on the critical path rather
  than decoration.
* ``parallel-permission`` and ``parallel-permission-empty-id`` hold two
  permission requests open at once, the way Claude Code's parallel tool calls
  do, with distinct and with colliding tool-call ids respectively.

Usage: ``scripted_acp_agent.py <log-path> <mode>``. The log is JSONL; each line
is ``{"direction": ..., "message": ...}`` so a test can assert on both the ACP
traffic and the MCP traffic this agent generated.
"""

import json
import os
import pathlib
import subprocess
import sys

LOG_PATH = pathlib.Path(sys.argv[1])
MODE = sys.argv[2] if len(sys.argv) > 2 else "mcp"
SESSION_ID = "scripted-session"
PERMISSION_REQUEST_ID = 900


def record(direction, message):
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"direction": direction, "message": message}, separators=(",", ":")
            )
            + "\n"
        )


def emit(message):
    record("agent->client", message)
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id, value):
    emit({"jsonrpc": "2.0", "id": request_id, "result": value})


def chunk(kind, text):
    emit(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": SESSION_ID,
                "update": {
                    "sessionUpdate": kind,
                    "content": {"type": "text", "text": text},
                },
            },
        }
    )


def read_client_message():
    line = sys.stdin.readline()
    if not line:
        return None
    message = json.loads(line)
    record("client->agent", message)
    return message


class StdioMcpClient:
    """A minimal MCP client over a stdio server, i.e. the Lemma bridge."""

    def __init__(self, spec):
        env = dict(os.environ)
        for variable in spec.get("env") or []:
            env[str(variable["name"])] = str(variable["value"])
        self.process = subprocess.Popen(  # noqa: S603
            [str(spec["command"]), *[str(arg) for arg in spec.get("args") or []]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def _send(self, message):
        record("agent->mcp", message)
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method, params=None):
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            }
        )
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP bridge closed before answering {method}")
        message = json.loads(line)
        record("mcp->agent", message)
        return message

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=30)


def stdio_server(mcp_servers):
    for server in mcp_servers or []:
        if isinstance(server, dict) and server.get("command"):
            return server
    return None


def run_mcp_turn(mcp_servers):
    """Discover and call one Lemma tool, reporting the result as the answer."""
    server = stdio_server(mcp_servers)
    if server is None:
        chunk("agent_message_chunk", "LEMMA_MCP_MISSING_STDIO_SERVER")
        return
    client = StdioMcpClient(server)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "scripted-acp-agent", "version": "1.0.0"},
            },
        )
        client.notify("notifications/initialized")
        listed = client.request("tools/list")
        names = [
            tool.get("name")
            for tool in (listed.get("result") or {}).get("tools") or []
        ]
        chunk("agent_message_chunk", "LEMMA_MCP_TOOLS:" + ",".join(names))
        if "lemma_echo" not in names:
            return
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": SESSION_ID,
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "mcp-call-1",
                        "title": "lemma_echo",
                        "kind": "other",
                        "status": "in_progress",
                    },
                },
            }
        )
        called = client.request(
            "tools/call",
            {"name": "lemma_echo", "arguments": {"text": "LEMMA_MCP_ROUND_TRIP"}},
        )
        blocks = (called.get("result") or {}).get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict)
        )
        chunk("agent_message_chunk", "LEMMA_MCP_RESULT:" + text)
    finally:
        client.close()


def run_permission_turn():
    """Ask for a native tool, then say which way the decision went."""
    emit(
        {
            "jsonrpc": "2.0",
            "id": PERMISSION_REQUEST_ID,
            "method": "session/request_permission",
            "params": {
                "sessionId": SESSION_ID,
                "toolCall": {
                    "toolCallId": "native-shell",
                    "status": "pending",
                    "kind": "execute",
                    "title": "Run rm -rf build",
                    "rawInput": {"command": "rm -rf build"},
                },
                "options": [
                    {"optionId": "reject", "name": "No", "kind": "reject_once"},
                    {"optionId": "once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "always", "name": "Always", "kind": "allow_always"},
                ],
            },
        }
    )
    answer = None
    while answer is None:
        message = read_client_message()
        if message is None:
            return
        if message.get("id") == PERMISSION_REQUEST_ID:
            answer = message
    report_outcome("LEMMA_PERMISSION", answer)


def report_outcome(prefix, answer):
    if "error" in answer:
        chunk(
            "agent_message_chunk",
            f"{prefix}_ERROR:" + str((answer.get("error") or {}).get("message")),
        )
        return
    outcome = (answer.get("result") or {}).get("outcome") or {}
    if outcome.get("outcome") == "selected":
        chunk("agent_message_chunk", f"{prefix}_ALLOWED:" + outcome["optionId"])
    else:
        chunk(
            "agent_message_chunk",
            f"{prefix}_DENIED:" + str(outcome.get("outcome")),
        )


def run_parallel_permission_turn(tool_call_ids):
    """Two permission requests in flight at once.

    Claude Code issues parallel tool calls, so two permission requests can be
    open in one session at the same time. Both are emitted before either answer
    is read, so they really are concurrent rather than sequential.

    ``tool_call_ids`` is what each request puts in ``toolCall.toolCallId``; a
    test uses it to choose between distinct ids and colliding ones.
    """
    requests = dict(zip((901, 902), ("Run tool A", "Run tool B")))
    for (request_id, title), tool_call_id in zip(requests.items(), tool_call_ids):
        emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": SESSION_ID,
                    "toolCall": {
                        "toolCallId": tool_call_id,
                        "status": "pending",
                        "kind": "execute",
                        "title": title,
                    },
                    "options": [
                        {"optionId": "reject", "name": "No", "kind": "reject_once"},
                        {
                            "optionId": "once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        },
                    ],
                },
            }
        )
    answers = {}
    while len(answers) < len(requests):
        message = read_client_message()
        if message is None:
            return
        if message.get("id") in requests:
            answers[message["id"]] = message
    for index, request_id in enumerate(sorted(requests)):
        report_outcome(f"LEMMA_PARALLEL_{'AB'[index]}", answers[request_id])


def main():
    mcp_servers = []
    while True:
        message = read_client_message()
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            result(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": False,
                        "mcpCapabilities": {"http": False, "sse": False},
                        "promptCapabilities": {"image": False, "audio": False},
                    },
                    "authMethods": [],
                    "agentInfo": {"name": "scripted-acp", "version": "1.0.0"},
                },
            )
        elif method == "session/new":
            mcp_servers = (message.get("params") or {}).get("mcpServers") or []
            result(request_id, {"sessionId": SESSION_ID, "configOptions": []})
        elif method == "session/prompt":
            if MODE == "mcp":
                run_mcp_turn(mcp_servers)
            elif MODE == "parallel-permission":
                run_parallel_permission_turn(("call-a", "call-b"))
            elif MODE == "parallel-permission-empty-id":
                run_parallel_permission_turn(("", ""))
            else:
                run_permission_turn()
            result(request_id, {"stopReason": "end_turn"})
        elif method == "session/cancel":
            continue
        elif request_id is not None:
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"unsupported method {method}",
                    },
                }
            )


main()
